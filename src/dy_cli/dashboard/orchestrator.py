"""受治理的跨账号编排层（对齐 xiaohongshu-matrices-cli orchestrator）。

在**不改动任何现有手动流程**的前提下，补上项目缺失的"跨账号编排 / 批量发布 /
批量互动"能力，并一律经由治理引擎（PII / opt-out / 高风险 / 相似度去重）把关，
统一限流，且全程问责（publish_tasks + orch_markers 记账）。

所有"跨账号自动"行为都是 **opt-in**，默认完全不启用：

- DY_ORCHESTRATOR=1               启动编排常驻线程
- DY_AUTO_PUBLISH=1|approve       自动执行 pending_review 发布任务
- DY_ENGAGEMENT_MODE              shadow / inbound / reviewed（互动灰度）
- DY_DAILY_PUBLISH_LIMIT=5        每账号每日最多自动发布数
- DY_ORCHESTRATOR_TICK=60         编排线程轮询间隔（秒）
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from fastapi import APIRouter, FastAPI

from .config import DashboardConfig
from .collector import DouyinCollector
from .db import Database
from .engagement import EngagementBlocked, EngagementGovernor
from .governance import evaluate_content
from .persistence import P0Store
from .queue import DurableTaskQueue, RunResult
from .rate_limit import AccountRateLimiter
from .utils import json_dumps, json_loads, now_iso


class Orchestrator:
    def __init__(
        self,
        db: Database,
        config: DashboardConfig,
        rate_limiter: AccountRateLimiter | None = None,
    ) -> None:
        self.db = db
        self.config = config
        # Shared store: rate limiting + durable task queue live in one SQLite file.
        self.store = P0Store(config.data_dir / "rate_limit.sqlite3")
        self.rate_limiter = rate_limiter or AccountRateLimiter(
            self.store,
            interval_seconds=config.request_interval_seconds,
            daily_limit=config.daily_request_limit,
        )
        self.queue = DurableTaskQueue(
            self.store,
            self.queue_runner,
            workers=config.worker_threads,
            lease_seconds=config.queue_lease_seconds,
            poll_seconds=config.queue_poll_seconds,
        )
        # 互动治理（灰度模式 / 暖线索 / 预算 / 停止名单）
        self.engagement = EngagementGovernor(self.db)
        # 采集层（搜索 / 评论 / 素材库），网络来源可注入
        self.collector = DouyinCollector(self.db)

        self.enabled = os.getenv("DY_ORCHESTRATOR") == "1"
        self.auto_publish_mode = os.getenv("DY_AUTO_PUBLISH", "")
        self.mode = os.getenv("DY_ENGAGEMENT_MODE", "shadow").strip().lower()
        self.tick_seconds = max(10, int(os.getenv("DY_ORCHESTRATOR_TICK", "60")))
        self.daily_publish_limit = max(1, int(os.getenv("DY_DAILY_PUBLISH_LIMIT", "5")))

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── 去重历史（per-account，持久化于 orch_markers）──────────────────────
    def _recent(self, account_id: int) -> list[str]:
        raw = self.db.get_marker(f"pub_hist:{account_id}")
        return json_loads(raw, []) if raw else []

    def _record(self, account_id: int, body: str) -> None:
        hist = self._recent(account_id)[-19:] + [body]
        self.db.set_marker(f"pub_hist:{account_id}", json_dumps(hist))

    def _published_today(self, account_id: int) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) n FROM publish_tasks WHERE account_id=? AND status='published' "
            "AND date(created_at)=date('now')",
            (account_id,),
        )
        return int((row or {}).get("n", 0))

    # ── 目标账号解析 ────────────────────────────────────────────────────
    def _target_accounts(self, account_ids: list[int] | None, group: str | None) -> list[dict[str, Any]]:
        accounts = [a for a in self.db.list_accounts(enabled_only=True)
                    if a.get("login_status") in ("ready", "legacy")]
        if account_ids:
            wanted = set(account_ids)
            accounts = [a for a in accounts if int(a["id"]) in wanted]
        if group:
            accounts = [a for a in accounts if a.get("group_name") == group]
        return accounts

    # ── 任务创建 / 执行 ─────────────────────────────────────────────────
    def create_publish_task(
        self,
        account_id: int,
        title: str,
        body: str,
        topics: list[str] | None = None,
        media_type: str = "video",
        media_paths: list[str] | None = None,
    ) -> int:
        fingerprint = str(abs(hash(body)))
        return self.db.create_publish_task(
            account_id, title, body, topics, media_type, media_paths, content_fingerprint=fingerprint
        )

    def run_publish_task(self, task_id: int) -> dict[str, Any]:
        """Governance + dedup + rate-limit gated execution of one publish task."""
        task = self.db.get_publish_task(task_id)
        if not task:
            return {"ok": False, "reason": "no task"}
        account = self.db.get_account(int(task["account_id"]))
        if not account:
            self.db.update("publish_tasks", task_id, status="failed", error="账号不存在")
            return {"ok": False, "reason": "no account"}

        alias = account["alias"]
        previous = self._recent(int(task["account_id"]))
        policy = evaluate_content(task["body"], previous=previous, threshold=0.85)
        if policy.decision == "block":
            self.db.update("publish_tasks", task_id, status="blocked", error="; ".join(policy.reasons))
            self.db.set_marker(f"pub_blocked:{task_id}", "; ".join(policy.reasons))
            return {"ok": False, "reason": "governance_block", "details": policy.reasons}

        self.rate_limiter.acquire(int(task["account_id"]))
        try:
            from . import publisher

            media_paths = json_loads(task["media_paths_json"], [])
            publisher.publish_for_account(
                alias,
                title=task["title"],
                body=task["body"],
                media_type=task["media_type"],
                media_paths=media_paths,
                tags=json_loads(task["topics_json"], []),
            )
            self.db.update("publish_tasks", task_id, status="published", attempts=task["attempts"] + 1)
            self.db.update("accounts", int(account["id"]), last_publish_at=now_iso())
            self._record(int(task["account_id"]), task["body"])
            return {"ok": True, "account": alias}
        except Exception as e:  # 单任务失败不阻断其余账号
            self.db.update(
                "publish_tasks", task_id, status="failed", error=str(e)[:500], attempts=task["attempts"] + 1
            )
            self.db.update("accounts", int(account["id"]), last_error=str(e)[:500])
            return {"ok": False, "reason": "execute_failed", "error": str(e)[:500]}

    # ── 批量编排（对外 API）────────────────────────────────────────────
    def queue_runner(self, item: Any) -> RunResult:
        """Queue worker dispatcher: routes by task kind to the right executor."""
        if item.kind == "search":
            return self.search_runner(item)
        return self.publish_runner(item)

    def publish_runner(self, item: Any) -> RunResult:
        """Queue worker: execute one publish task and map its result to a terminal status."""
        result = self.run_publish_task(int(item.resource_id))
        if result.get("ok"):
            return RunResult("done")
        reason = result.get("reason")
        if reason == "governance_block":
            return RunResult("failed", error="; ".join(result.get("details") or []))
        return RunResult("failed", error=result.get("error") or reason)

    def search_runner(self, item: Any) -> RunResult:
        """Queue worker: run one search job; maps collector status to a terminal status."""
        status = self.collector.run(int(item.resource_id))
        if status == "complete":
            return RunResult("done")
        if status in {"cancelled", "paused"}:
            return RunResult("manual", error=status)
        return RunResult("failed", error="collector: " + status, retryable=True)

    def run_search_job(self, job_id: int, account_id: int | None = None) -> int:
        """Enqueue a search job onto the durable queue (crash-safe, resumable)."""
        return self.queue.enqueue("search", job_id, account_id, max_attempts=3)

    def batch_publish(
        self,
        spec: dict[str, Any],
        account_ids: list[int] | None = None,
        group: str | None = None,
    ) -> list[int]:
        """为命中的启用账号各建一个发布任务；若编排已启用则入队由持久队列执行。"""
        targets = self._target_accounts(account_ids, group)
        created: list[int] = []
        for acc in targets:
            if self._published_today(int(acc["id"])) >= self.daily_publish_limit:
                continue
            tid = self.create_publish_task(
                int(acc["id"]),
                spec.get("title", ""),
                spec.get("body", ""),
                spec.get("topics"),
                spec.get("media_type", "video"),
                spec.get("media_paths"),
            )
            created.append(tid)
            if self.enabled:
                self.db.update("publish_tasks", tid, status="queued")
                self.queue.enqueue("publish", tid, int(acc["id"]), max_attempts=2)
        return created

    def batch_interact(
        self,
        aweme_id: str,
        action: str,
        account_ids: list[int] | None = None,
        group: str | None = None,
        content: str | None = None,
        *,
        target_user_id: str | None = None,
        warm_lead: bool = False,
    ) -> list[dict[str, Any]]:
        """对命中的启用账号批量执行同一互动（受互动治理层 preflight 把关）。"""
        targets = self._target_accounts(account_ids, group)
        results: list[dict[str, Any]] = []
        for acc in targets:
            account_id = int(acc["id"])
            # 互动治理：灰度模式 / 账号态 / opt-out / 暖线索 / 停止名单 / 预算
            try:
                self.engagement.preflight(
                    account_id,
                    action,
                    target_user_id=target_user_id,
                    content=content,
                    warm_lead=warm_lead,
                )
            except EngagementBlocked as e:
                results.append({"account": acc["alias"], "ok": False, "reason": "engagement_block", "error": str(e)})
                continue
            # 互动内容若存在，仍需检测 PII/opt-out（治理引擎）
            if content:
                policy = evaluate_content(content, threshold=0.85)
                if policy.decision == "block":
                    results.append({"account": acc["alias"], "ok": False, "reason": "governance_block"})
                    continue
            self.rate_limiter.acquire(account_id)
            try:
                from . import publisher

                r = publisher.interact_for_account(acc["alias"], aweme_id, action, content=content)
                self.engagement.record(account_id, action, target_user_id or "", content or "")
                results.append({"account": acc["alias"], "ok": bool(r.get("success")), "result": r})
            except Exception as e:
                results.append({"account": acc["alias"], "ok": False, "error": str(e)[:200]})
        return results

    # ── 常驻线程（opt-in）──────────────────────────────────────────────
    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dy-orchestrator", daemon=True)
        self._thread.start()
        # 持久队列随之启动：崩溃可恢复，任务不丢
        self.queue.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # 单轮异常不应杀死常驻线程
                self.db.set_marker(f"last_error:{now_iso()}", str(exc)[:500])
            self._stop.wait(self.tick_seconds)

    def _tick(self) -> None:
        # 自动发布：把已批准（pending_review）的任务入队，交持久队列执行（崩溃可恢复）
        if self.auto_publish_mode:
            for task in self.db.list_publish_tasks():
                if task["status"] != "pending_review":
                    continue
                if self._published_today(int(task["account_id"])) >= self.daily_publish_limit:
                    continue
                self.db.update("publish_tasks", int(task["id"]), status="queued")
                self.queue.enqueue("publish", int(task["id"]), int(task["account_id"]), max_attempts=2)

    # ── HTTP 接口（只读状态 + 手动触发一轮）────────────────────────────
    def install(self, app: FastAPI) -> None:
        router = APIRouter()

        @router.get("/api/orchestrator/status")
        def status():
            return {
                "ok": True,
                "data": {
                    "enabled": self.enabled,
                    "auto_publish": self.auto_publish_mode or False,
                    "mode": self.mode,
                    "daily_publish_limit": self.daily_publish_limit,
                    "tick_seconds": self.tick_seconds,
                    "running": bool(self._thread and self._thread.is_alive()),
                    "queue": self.store.queue_counts(),
                },
            }

        @router.post("/api/orchestrator/trigger")
        def trigger():
            if not self.enabled:
                return {"ok": False, "error": "orchestrator disabled (set DY_ORCHESTRATOR=1)"}
            self._tick()
            return {"ok": True}

        @router.get("/api/engagement/status")
        def engagement_status():
            return {"ok": True, "data": self.engagement.status()}

        app.include_router(router)
