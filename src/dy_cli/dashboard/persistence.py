"""Persistent per-account request pacing store (对齐 xiaohongshu-matrices-cli)。

Backed by a small SQLite file so interval / daily-limit / pause state survives
process restarts. Used by ``rate_limit.AccountRateLimiter``.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
try:
    from datetime import UTC
except ImportError:  # Python < 3.11
    from datetime import timezone
    UTC = timezone.utc
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QueueItem:
    id: int
    kind: str
    resource_id: int
    account_id: int | None
    attempts: int
    max_attempts: int


class P0Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        with self._lock, self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS rate_state (
                  account_id INTEGER PRIMARY KEY,
                  last_request_ts REAL NOT NULL DEFAULT 0,
                  daily_date TEXT NOT NULL DEFAULT '',
                  daily_count INTEGER NOT NULL DEFAULT 0,
                  pause_until_ts REAL NOT NULL DEFAULT 0,
                  pause_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS task_queue (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  kind TEXT NOT NULL CHECK(kind IN ('publish','search','engagement')),
                  resource_id INTEGER NOT NULL,
                  account_id INTEGER,
                  status TEXT NOT NULL DEFAULT 'queued',
                  available_at TEXT NOT NULL,
                  lease_until TEXT,
                  heartbeat_at TEXT,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL DEFAULT 2,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  last_error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_task_queue_claim
                  ON task_queue(status,available_at,id);
                CREATE INDEX IF NOT EXISTS idx_task_queue_account
                  ON task_queue(account_id,status);
                """
            )

    # ── durable task queue (对齐 xhs: 租约/心跳/恢复/幂等)──────────────────
    def enqueue(
        self,
        kind: str,
        resource_id: int,
        account_id: int | None,
        *,
        max_attempts: int = 2,
    ) -> int:
        if kind not in {"publish", "search", "engagement"}:
            raise ValueError("unsupported queue task kind")
        key = f"{kind}:{resource_id}"
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT id,status FROM task_queue WHERE idempotency_key=?", (key,)).fetchone()
            if row and row["status"] in {"queued", "running", "retry_wait"}:
                con.commit()
                return int(row["id"])
            if row:
                con.execute(
                    """UPDATE task_queue SET account_id=?,status='queued',available_at=?,lease_until=NULL,
                    heartbeat_at=NULL,attempts=0,max_attempts=?,last_error=NULL,updated_at=? WHERE id=?""",
                    (account_id, now, max_attempts, now, row["id"]),
                )
                queue_id = int(row["id"])
            else:
                cursor = con.execute(
                    """INSERT INTO task_queue(kind,resource_id,account_id,status,available_at,max_attempts,
                    idempotency_key,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?,?)""",
                    (kind, resource_id, account_id, now, max_attempts, key, now, now),
                )
                queue_id = int(cursor.lastrowid)
            con.commit()
            return queue_id

    def recover_expired(self) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                "SELECT * FROM task_queue WHERE status='running' AND lease_until IS NOT NULL AND lease_until<?",
                (now,),
            ).fetchall()
            for row in rows:
                if row["kind"] == "publish":
                    # 发布中途崩溃：结果必须人工核验，绝不自动重发
                    message = "发布执行期间服务中断；结果须人工核验，系统不会自动重发"
                    con.execute(
                        "UPDATE task_queue SET status='manual',last_error=?,lease_until=NULL,updated_at=? WHERE id=?",
                        (message, now, row["id"]),
                    )
                else:
                    con.execute(
                        "UPDATE task_queue SET status='queued',available_at=?,lease_until=NULL,updated_at=? WHERE id=?",
                        (now, now, row["id"]),
                    )
            con.commit()

    def claim(self, lease_seconds: int = 180) -> QueueItem | None:
        self.recover_expired()
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        lease = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT q.* FROM task_queue q
                WHERE q.status IN ('queued','retry_wait') AND q.available_at<=?
                AND (q.account_id IS NULL OR NOT EXISTS(
                    SELECT 1 FROM task_queue active
                    WHERE active.account_id=q.account_id AND active.status='running'))
                ORDER BY q.available_at,q.id LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                con.commit()
                return None
            con.execute(
                """UPDATE task_queue SET status='running',lease_until=?,heartbeat_at=?,
                attempts=attempts+1,updated_at=? WHERE id=?""",
                (lease, now, now, row["id"]),
            )
            con.commit()
            return QueueItem(
                int(row["id"]),
                str(row["kind"]),
                int(row["resource_id"]),
                int(row["account_id"]) if row["account_id"] is not None else None,
                int(row["attempts"]) + 1,
                int(row["max_attempts"]),
            )

    def heartbeat(self, queue_id: int, lease_seconds: int = 180) -> None:
        now_dt = datetime.now(UTC)
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE task_queue SET heartbeat_at=?,lease_until=?,updated_at=? WHERE id=? AND status='running'",
                (
                    now_dt.isoformat(),
                    (now_dt + timedelta(seconds=lease_seconds)).isoformat(),
                    now_dt.isoformat(),
                    queue_id,
                ),
            )
            con.commit()

    def finish(
        self,
        item: QueueItem,
        status: str,
        error: str | None = None,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        now_dt = datetime.now(UTC)
        if retryable and item.attempts < item.max_attempts:
            status = "retry_wait"
            delay = retry_after_seconds or min(300, 5 * (2 ** (item.attempts - 1)))
            available = (now_dt + timedelta(seconds=delay)).isoformat()
        else:
            available = now_dt.isoformat()
            if retryable:
                status = "failed"
        with self._lock, self._connect() as con:
            con.execute(
                """UPDATE task_queue SET status=?,available_at=?,lease_until=NULL,heartbeat_at=NULL,
                last_error=?,updated_at=? WHERE id=?""",
                (status, available, error, now_dt.isoformat(), item.id),
            )
            con.commit()

    def cancel(self, kind: str, resource_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE task_queue SET status='cancelled',lease_until=NULL,updated_at=? "
                "WHERE idempotency_key=? AND status IN ('queued','retry_wait')",
                (datetime.now(UTC).isoformat(), f"{kind}:{resource_id}"),
            )
            con.commit()

    def queue_counts(self) -> dict[str, int]:
        rows = self._safe_fetchall("SELECT status,COUNT(*) count FROM task_queue GROUP BY status")
        return {str(row["status"]): int(row["count"]) for row in rows}

    def _safe_fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list:
        with self._lock, self._connect() as con:
            return con.execute(sql, params).fetchall()

    def acquire_request(self, account_id: int, interval_seconds: float, daily_limit: int) -> float:
        """Return seconds to wait before the request is allowed (0 = allowed now)."""
        now = time.time()
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT * FROM rate_state WHERE account_id=?", (account_id,)
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO rate_state(account_id,last_request_ts,daily_date,daily_count)"
                    " VALUES(?,?,?,?)",
                    (account_id, 0.0, "", 0),
                )
                last = 0.0
                daily_date = ""
                daily_count = 0
                pause_until = 0.0
            else:
                last = row["last_request_ts"]
                daily_date = row["daily_date"]
                daily_count = row["daily_count"]
                pause_until = row["pause_until_ts"]

            # 1) hard pause
            if pause_until and now < pause_until:
                return max(0.0, pause_until - now)

            # 2) daily limit
            today = time.strftime("%Y-%m-%d", time.localtime(now))
            if daily_date != today:
                daily_date, daily_count = today, 0
            if daily_count >= daily_limit:
                next_mid = _dt.datetime.combine(
                    _dt.date.today() + _dt.timedelta(days=1), _dt.time.min
                )
                return max(0.0, (next_mid - _dt.datetime.now()).total_seconds())

            # 3) per-request interval
            wait = max(0.0, last + interval_seconds - now)
            if wait <= 0:
                con.execute(
                    "UPDATE rate_state SET last_request_ts=?, daily_date=?, daily_count=? WHERE account_id=?",
                    (now, daily_date, daily_count + 1, account_id),
                )
                return 0.0
            return wait

    def pause_account(self, account_id: int, seconds: int, reason: str) -> None:
        until = time.time() + seconds
        with self._lock, self._connect() as con:
            con.execute(
                """INSERT INTO rate_state(account_id, pause_until_ts, pause_reason)
                   VALUES(?,?,?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     pause_until_ts=excluded.pause_until_ts,
                     pause_reason=excluded.pause_reason""",
                (account_id, until, reason or ""),
            )
