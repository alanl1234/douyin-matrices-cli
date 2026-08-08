"""互动治理层（对齐 xiaohongshu-matrices-cli engagement 的灰度 + 暖线索 + 预算）。

三段式互动灰度（受 ``DY_ENGAGEMENT_MODE`` 控制，默认 ``shadow``）：
- ``shadow``   只生成/审核草稿，绝不发送；
- ``inbound``  仅放开自有评论回复与入站私信，主动触达（comment / dm_outbound）关闭；
- ``reviewed`` 全部放开（仍受治理引擎与预算约束）。

所有发送都经过：账号启用/登录态校验、opt-out / 敏感信息检测、主动私信的暖线索门控、
目标停止名单与跨账号冷却、以及每账号每动作日限额 / 评论每小时合并限额 / 私信最小间隔。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
try:
    from datetime import UTC
except ImportError:  # Python < 3.11
    from datetime import timezone
    UTC = timezone.utc
from typing import Any

from .governance import contains_opt_out, contains_sensitive_information
from .utils import now_iso


class EngagementBlocked(RuntimeError):
    """Raised when an engagement action is refused by the governance layer."""


@dataclass(frozen=True)
class EngagementRule:
    similarity_threshold: float = 0.85
    comment_reply_daily: int = 50
    comment_daily: int = 30
    dm_outbound_daily: int = 10
    dm_reply_daily: int = 40
    comment_hourly_combined: int = 10
    target_cooldown_days: int = 7
    dm_outbound_interval_seconds: int = 600
    dm_reply_interval_seconds: int = 300


DEFAULT_RULE = EngagementRule()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class EngagementGovernor:
    def __init__(self, db: Any, rule: EngagementRule | None = None, mode: str | None = None) -> None:
        self.db = db
        self.rule = rule or DEFAULT_RULE
        self.mode = (mode or os.getenv("DY_ENGAGEMENT_MODE", "shadow")).strip().lower()

    # ── preflight gate ─────────────────────────────────────────────────
    def preflight(
        self,
        account_id: int,
        kind: str,
        *,
        target_user_id: str | None = None,
        content: str | None = None,
        warm_lead: bool = False,
    ) -> None:
        # 1) 灰度模式
        if self.mode == "shadow":
            raise EngagementBlocked("当前为影子模式，只生成和审核草稿，不执行发送")
        if self.mode == "inbound" and kind in {"comment", "dm_outbound"}:
            raise EngagementBlocked("当前仅开放自有评论回复和入站私信，主动触达仍处于关闭状态")
        if self.mode not in {"inbound", "reviewed"}:
            raise EngagementBlocked("无效的 DY_ENGAGEMENT_MODE，必须为 shadow、inbound 或 reviewed")

        # 2) 账号启用 + 登录态
        acc = self.db.get_account(account_id)
        if not acc or not acc.get("enabled"):
            raise EngagementBlocked("账号已停用，账号级停止开关生效")
        if acc.get("login_status") not in {"ready", "legacy"}:
            raise EngagementBlocked("账号当前不可执行互动，请先处理登录或账号异常")

        # 3) 入站内容安全（opt-out / 敏感信息）
        if content is not None:
            if contains_opt_out(content):
                if target_user_id:
                    self.db.block_target(target_user_id, "opt_out")
                raise EngagementBlocked("对方已拒绝继续联系（opt-out）")
            if contains_sensitive_information(content):
                raise EngagementBlocked("检测到敏感信息，请人工处理")

        # 4) 主动私信的暖线索门控（warm_lead 由上游根据明确意向行为判定）
        if kind == "dm_outbound" and not warm_lead:
            raise EngagementBlocked("主动私信仅允许明确意向行为形成的暖线索")

        # 5) 目标停止名单 + 跨账号冷却
        if target_user_id:
            contact = self.db.get_target_contact(target_user_id)
            if contact and contact.get("blocked"):
                raise EngagementBlocked("目标已拒绝触达或被加入停止名单")
            if kind in {"dm_outbound", "comment"} and contact and contact.get("last_contact_at"):
                last = _parse_iso(contact["last_contact_at"])
                if last and (datetime.now(UTC) - last) < timedelta(days=self.rule.target_cooldown_days):
                    raise EngagementBlocked("该用户仍处于跨账号触达冷却期")

        # 6) 预算 / 间隔
        self._check_budget(account_id, kind)

    def _check_budget(self, account_id: int, kind: str) -> None:
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        hour_start = now.replace(minute=0, second=0, microsecond=0).isoformat()

        limits = {
            "comment_reply": self.rule.comment_reply_daily,
            "comment": self.rule.comment_daily,
            "dm_outbound": self.rule.dm_outbound_daily,
            "dm_reply": self.rule.dm_reply_daily,
        }
        daily = self.db.count_engagement_since(account_id, kind, day_start)
        if daily >= limits[kind]:
            raise EngagementBlocked(f"已达到 {kind} 每账号日限额 {limits[kind]}")

        if kind in {"comment", "comment_reply"}:
            hourly = self.db.count_engagement_hourly(account_id, ["comment", "comment_reply"], hour_start)
            if hourly >= self.rule.comment_hourly_combined:
                raise EngagementBlocked("评论与回复合计已达到每小时限额")

        if kind == "dm_outbound":
            self._check_interval(account_id, "dm_outbound", self.rule.dm_outbound_interval_seconds)
        elif kind == "dm_reply":
            self._check_interval(account_id, "dm_reply", self.rule.dm_reply_interval_seconds)

    def _check_interval(self, account_id: int, kind: str, seconds: int) -> None:
        last = _parse_iso(self.db.last_engagement_at(account_id, kind))
        if not last:
            return
        remaining = seconds - (datetime.now(UTC) - last).total_seconds()
        if remaining > 0:
            raise EngagementBlocked(f"最小发送间隔未满足，还需等待 {int(remaining) + 1} 秒")

    # ── recording ─────────────────────────────────────────────────────
    def record(self, account_id: int, kind: str, target_user_id: str = "", content: str = "") -> None:
        self.db.record_engagement_action(account_id, kind, target_user_id or "", content or "")

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "rule": {k: getattr(self.rule, k) for k in self.rule.__dataclass_fields__}}
