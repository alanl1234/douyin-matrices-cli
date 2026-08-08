"""SQLite repository for the douyin-matrices dashboard.

Holds the account matrix (accounts + personas) plus publishing / orchestration
state. Mirrors xiaohongshu-matrices-cli's schema, adapted for Douyin
(aweme / douyin_user_id / storage_state cookie files).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .utils import json_dumps, now_iso

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 alias TEXT NOT NULL UNIQUE,
 douyin_user_id TEXT NOT NULL DEFAULT '',
 nickname TEXT NOT NULL DEFAULT '',
 cookie_file TEXT NOT NULL DEFAULT '',
 login_status TEXT NOT NULL DEFAULT 'unbound',
 persona_id INTEGER,
 group_name TEXT NOT NULL DEFAULT '',
 enabled INTEGER NOT NULL DEFAULT 1,
 last_publish_at TEXT,
 last_verified_at TEXT,
 last_error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS personas (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL UNIQUE,
 tone TEXT NOT NULL DEFAULT '',
 bio TEXT NOT NULL DEFAULT '',
 topics_json TEXT NOT NULL DEFAULT '[]',
 forbidden_words_json TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS publish_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 account_id INTEGER NOT NULL,
 title TEXT NOT NULL DEFAULT '',
 body TEXT NOT NULL DEFAULT '',
 topics_json TEXT NOT NULL DEFAULT '[]',
 media_type TEXT NOT NULL DEFAULT 'video',
 media_paths_json TEXT NOT NULL DEFAULT '[]',
 visibility TEXT NOT NULL DEFAULT '公开',
 schedule_at TEXT NOT NULL DEFAULT '',
 mentions_json TEXT NOT NULL DEFAULT '[]',
 result_url TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending_review',
 attempts INTEGER NOT NULL DEFAULT 0,
 content_fingerprint TEXT,
 error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(account_id) REFERENCES accounts(id));

CREATE TABLE IF NOT EXISTS orch_markers (
 key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS engagement_actions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 account_id INTEGER NOT NULL,
 kind TEXT NOT NULL,
 target_user_id TEXT NOT NULL DEFAULT '',
 content TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 FOREIGN KEY(account_id) REFERENCES accounts(id));

CREATE TABLE IF NOT EXISTS target_contacts (
 external_user_id TEXT PRIMARY KEY,
 blocked INTEGER NOT NULL DEFAULT 0,
 block_reason TEXT NOT NULL DEFAULT '',
 last_account_id INTEGER,
 last_contact_at TEXT,
 updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS search_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL, account_id INTEGER,
 keywords_json TEXT NOT NULL DEFAULT '[]', topics_json TEXT NOT NULL DEFAULT '[]',
 media_type TEXT NOT NULL DEFAULT 'all', include_comments INTEGER NOT NULL DEFAULT 1,
 comment_limit INTEGER NOT NULL DEFAULT 100, max_pages INTEGER NOT NULL DEFAULT 3,
 min_likes INTEGER NOT NULL DEFAULT 0, min_shares INTEGER NOT NULL DEFAULT 0,
 min_comments INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending',
 progress_current INTEGER NOT NULL DEFAULT 0, progress_total INTEGER NOT NULL DEFAULT 0,
 result_count INTEGER NOT NULL DEFAULT 0, error TEXT,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL DEFAULT '',
 FOREIGN KEY(account_id) REFERENCES accounts(id));

CREATE TABLE IF NOT EXISTS notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 aweme_id TEXT NOT NULL UNIQUE,
 author_id TEXT NOT NULL DEFAULT '', author_name TEXT NOT NULL DEFAULT '',
 desc TEXT NOT NULL DEFAULT '', publish_time TEXT,
 media_type TEXT NOT NULL DEFAULT 'video',
 original_url TEXT NOT NULL DEFAULT '',
 likes INTEGER NOT NULL DEFAULT 0, shares INTEGER NOT NULL DEFAULT 0,
 comments INTEGER NOT NULL DEFAULT 0, collects INTEGER NOT NULL DEFAULT 0,
 topics_json TEXT NOT NULL DEFAULT '[]', covers_json TEXT NOT NULL DEFAULT '[]',
 comments_json TEXT NOT NULL DEFAULT '[]', raw_json TEXT NOT NULL DEFAULT '{}',
 collected_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS search_job_notes (
 job_id INTEGER NOT NULL, note_id INTEGER NOT NULL, PRIMARY KEY(job_id,note_id),
 FOREIGN KEY(job_id) REFERENCES search_jobs(id) ON DELETE CASCADE,
 FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE);

CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(login_status);
CREATE INDEX IF NOT EXISTS idx_publish_tasks_status ON publish_tasks(status);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            # Lightweight column migrations for existing DBs.
            self._ensure_column(con, "accounts", "last_verified_at", "TEXT")
            self._ensure_column(con, "accounts", "last_publish_at", "TEXT")
            self._ensure_column(con, "accounts", "last_error", "TEXT")
            self._ensure_column(con, "search_jobs", "updated_at", "TEXT")
            # 阶段 B：发布任务透传可见范围 / 定时 / @好友 + 作品链接回显
            self._ensure_column(con, "publish_tasks", "visibility", "TEXT")
            self._ensure_column(con, "publish_tasks", "schedule_at", "TEXT")
            self._ensure_column(con, "publish_tasks", "mentions_json", "TEXT")
            self._ensure_column(con, "publish_tasks", "result_url", "TEXT")

    def _ensure_column(self, con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        if any(r[1] == column for r in rows):
            return
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        con.commit()

    # ── generic helpers ───────────────────────────────────────────────
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock, self.connect() as con:
            cursor = con.execute(sql, params)
            con.commit()
            return int(cursor.lastrowid)

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    def update(self, table: str, row_id: int, **values: Any) -> None:
        if not values:
            return
        if table in {"accounts", "publish_tasks", "personas"}:
            values["updated_at"] = now_iso()
        assignments = ",".join(f"{key}=?" for key in values)
        self.execute(f"UPDATE {table} SET {assignments} WHERE id=?", (*values.values(), row_id))

    # ── accounts ──────────────────────────────────────────────────────
    def create_account(
        self,
        alias: str,
        cookie_file: str,
        douyin_user_id: str = "",
        nickname: str = "",
        login_status: str = "ready",
        persona_id: int | None = None,
        group_name: str = "",
        enabled: int = 1,
    ) -> int:
        now = now_iso()
        return self.execute(
            "INSERT INTO accounts(alias,cookie_file,douyin_user_id,nickname,login_status,"
            "persona_id,group_name,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                alias,
                cookie_file,
                douyin_user_id,
                nickname,
                login_status,
                persona_id,
                group_name,
                enabled,
                now,
                now,
            ),
        )

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))

    def get_account_by_alias(self, alias: str) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM accounts WHERE alias=?", (alias,))

    def resolve_account(self, identifier: str | int) -> dict[str, Any] | None:
        """Resolve by int id, alias, or douyin_user_id (in that order)."""
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            row = self.get_account(int(identifier))
            if row:
                return row
        ident = str(identifier)
        row = self.fetchone(
            "SELECT * FROM accounts WHERE alias=? OR douyin_user_id=?",
            (ident, ident),
        )
        return row

    def list_accounts(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM accounts ORDER BY id"
        if enabled_only:
            sql = "SELECT * FROM accounts WHERE enabled=1 ORDER BY id"
        return self.fetchall(sql)

    def delete_account(self, account_id: int) -> None:
        self.execute("DELETE FROM accounts WHERE id=?", (account_id,))

    def set_login_status(self, account_id: int, status: str, *, error: str | None = None) -> None:
        values: dict[str, Any] = {"login_status": status}
        if error is not None:
            values["last_error"] = error
        self.update("accounts", account_id, **values)

    def mark_verified(self, account_id: int, *, error: str | None = None) -> None:
        """Record a successful cookie/health verification (mirrors xhs last_verified_at)."""
        values: dict[str, Any] = {"last_verified_at": now_iso()}
        if error is not None:
            values["last_error"] = error
        self.update("accounts", account_id, **values)

    def get_account_health(self, account_id: int) -> dict[str, Any]:
        """Aggregate health signals for one account (used by /api/health)."""
        row = self.get_account(account_id)
        if not row:
            return {"account_id": account_id, "exists": False}
        has_cookie = bool(row.get("cookie_file")) and os.path.isfile(row.get("cookie_file") or "")
        return {
            "account_id": account_id,
            "exists": True,
            "alias": row.get("alias"),
            "login_status": row.get("login_status"),
            "enabled": bool(row.get("enabled")),
            "has_cookie_file": has_cookie,
            "last_verified_at": row.get("last_verified_at"),
            "last_publish_at": row.get("last_publish_at"),
            "last_error": row.get("last_error"),
            "healthy": bool(row.get("enabled")) and row.get("login_status") in ("ready", "legacy")
            and has_cookie,
        }

    # ── personas ──────────────────────────────────────────────────────
    def create_persona(
        self,
        name: str,
        tone: str = "",
        bio: str = "",
        topics: list[str] | None = None,
        forbidden_words: list[str] | None = None,
    ) -> int:
        now = now_iso()
        return self.execute(
            "INSERT INTO personas(name,tone,bio,topics_json,forbidden_words_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                name,
                tone,
                bio,
                json_dumps(topics or []),
                json_dumps(forbidden_words or []),
                now,
                now,
            ),
        )

    def get_persona(self, persona_id: int) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM personas WHERE id=?", (persona_id,))

    def get_persona_by_name(self, name: str) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM personas WHERE name=?", (name,))

    def list_personas(self) -> list[dict[str, Any]]:
        return self.fetchall("SELECT * FROM personas ORDER BY id")

    def delete_persona(self, persona_id: int) -> None:
        # detach accounts referencing this persona
        self.execute("UPDATE accounts SET persona_id=NULL,updated_at=? WHERE persona_id=?", (now_iso(), persona_id))
        self.execute("DELETE FROM personas WHERE id=?", (persona_id,))

    # ── publish tasks ─────────────────────────────────────────────────
    def create_publish_task(
        self,
        account_id: int,
        title: str,
        body: str,
        topics: list[str] | None = None,
        media_type: str = "video",
        media_paths: list[str] | None = None,
        content_fingerprint: str | None = None,
        visibility: str = "公开",
        schedule_at: str | None = None,
        mentions: list[str] | None = None,
        result_url: str = "",
    ) -> int:
        now = now_iso()
        return self.execute(
            "INSERT INTO publish_tasks(account_id,title,body,topics_json,media_type,"
            "media_paths_json,content_fingerprint,visibility,schedule_at,mentions_json,result_url,"
            "created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                account_id,
                title,
                body,
                json_dumps(topics or []),
                media_type,
                json_dumps(media_paths or []),
                content_fingerprint or "",
                visibility,
                schedule_at or "",
                json_dumps(mentions or []),
                result_url,
                now,
                now,
            ),
        )

    def get_publish_task(self, task_id: int) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM publish_tasks WHERE id=?", (task_id,))

    def list_publish_tasks(self, account_id: int | None = None) -> list[dict[str, Any]]:
        if account_id is None:
            return self.fetchall("SELECT * FROM publish_tasks ORDER BY id DESC")
        return self.fetchall(
            "SELECT * FROM publish_tasks WHERE account_id=? ORDER BY id DESC", (account_id,)
        )

    # ── orchestration markers (self-accounting, never mutates other tables) ──
    def set_marker(self, key: str, value: str = "1") -> None:
        if self.fetchone("SELECT key FROM orch_markers WHERE key=?", (key,)):
            self.execute("UPDATE orch_markers SET value=?,updated_at=? WHERE key=?", (value, now_iso(), key))
        else:
            self.execute(
                "INSERT INTO orch_markers(key,value,updated_at) VALUES(?,?,?)", (key, value, now_iso())
            )

    def get_marker(self, key: str) -> str:
        row = self.fetchone("SELECT value FROM orch_markers WHERE key=?", (key,))
        return row["value"] if row else ""

    def marker_exists(self, key: str) -> bool:
        return self.fetchone("SELECT key FROM orch_markers WHERE key=?", (key)) is not None

    # ── engagement (互动灰度 / 暖线索 / 预算 的持久层)─────────────────────
    def record_engagement_action(
        self, account_id: int, kind: str, target_user_id: str = "", content: str = ""
    ) -> None:
        self.execute(
            "INSERT INTO engagement_actions(account_id,kind,target_user_id,content,created_at) "
            "VALUES(?,?,?,?,?)",
            (account_id, kind, target_user_id or "", content or "", now_iso()),
        )
        if target_user_id:
            self.upsert_target_contact(target_user_id, last_account_id=account_id)

    def count_engagement_since(self, account_id: int, kind: str, since_iso: str) -> int:
        row = self.fetchone(
            "SELECT COUNT(*) n FROM engagement_actions WHERE account_id=? AND kind=? AND datetime(created_at)>=datetime(?)",
            (account_id, kind, since_iso),
        )
        return int((row or {}).get("n", 0))

    def count_engagement_hourly(self, account_id: int, kinds: list[str], since_iso: str) -> int:
        placeholders = ",".join("?" for _ in kinds)
        row = self.fetchone(
            f"SELECT COUNT(*) n FROM engagement_actions WHERE account_id=? AND kind IN ({placeholders}) "
            f"AND datetime(created_at)>=datetime(?)",
            (account_id, *kinds, since_iso),
        )
        return int((row or {}).get("n", 0))

    def get_target_contact(self, external_user_id: str) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM target_contacts WHERE external_user_id=?", (external_user_id,))

    def last_engagement_at(self, account_id: int, kind: str) -> str | None:
        row = self.fetchone(
            "SELECT created_at FROM engagement_actions WHERE account_id=? AND kind=? ORDER BY id DESC LIMIT 1",
            (account_id, kind),
        )
        return row["created_at"] if row else None

    def upsert_target_contact(
        self,
        external_user_id: str,
        *,
        blocked: int | None = None,
        block_reason: str | None = None,
        last_account_id: int | None = None,
    ) -> None:
        now = now_iso()
        existing = self.get_target_contact(external_user_id)
        if existing is None:
            self.execute(
                "INSERT INTO target_contacts(external_user_id,blocked,block_reason,last_account_id,last_contact_at,updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (
                    external_user_id,
                    1 if blocked else 0,
                    block_reason or "",
                    last_account_id,
                    now if last_account_id is not None else None,
                    now,
                ),
            )
            return
        values: dict[str, Any] = {"updated_at": now}
        if blocked is not None:
            values["blocked"] = 1 if blocked else 0
            if block_reason is not None:
                values["block_reason"] = block_reason
        if last_account_id is not None:
            values["last_account_id"] = last_account_id
            values["last_contact_at"] = now
        assignments = ",".join(f"{k}=?" for k in values)
        self.execute(
            f"UPDATE target_contacts SET {assignments} WHERE external_user_id=?",
            (*values.values(), external_user_id),
        )

    def block_target(self, external_user_id: str, reason: str) -> None:
        self.upsert_target_contact(external_user_id, blocked=1, block_reason=reason)

    # ── 采集层（search_jobs / notes / search_job_notes）──────────────────
    def create_search_job(self, values: dict[str, Any]) -> int:
        defaults = {
            "account_id": None,
            "keywords_json": "[]",
            "topics_json": "[]",
            "media_type": "all",
            "include_comments": 1,
            "comment_limit": 100,
            "max_pages": 3,
            "min_likes": 0,
            "min_shares": 0,
            "min_comments": 0,
        }
        defaults.update(values)
        fields = [
            "name", "account_id", "keywords_json", "topics_json", "media_type",
            "include_comments", "comment_limit", "max_pages",
            "min_likes", "min_shares", "min_comments",
        ]
        now = now_iso()
        marks = ",".join("?" for _ in fields)
        return self.execute(
            f"INSERT INTO search_jobs({','.join(fields)},created_at) VALUES({marks},?)",
            tuple(defaults[f] for f in fields) + (now,),
        )

    def set_search_job_status(self, job_id: int, status: str, *, error: str | None = None) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": now_iso()}
        if status == "running" and not self.fetchone("SELECT started_at FROM search_jobs WHERE id=?", (job_id,)):
            values["started_at"] = now_iso()
        if status in {"complete", "failed", "cancelled", "paused"}:
            values["finished_at"] = now_iso()
        if error is not None:
            values["error"] = error
        assignments = ",".join(f"{k}=?" for k in values)
        self.execute(f"UPDATE search_jobs SET {assignments} WHERE id=?", (*values.values(), job_id))

    def get_search_job(self, job_id: int) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM search_jobs WHERE id=?", (job_id,))

    def list_search_jobs(self) -> list[dict[str, Any]]:
        return self.fetchall("SELECT * FROM search_jobs ORDER BY id DESC")

    def upsert_note(self, note: dict[str, Any]) -> int:
        fields = [
            "author_id", "author_name", "desc", "publish_time", "media_type",
            "original_url", "likes", "shares", "comments", "collects",
            "topics_json", "covers_json", "comments_json", "raw_json",
        ]
        existing = self.fetchone("SELECT id FROM notes WHERE aweme_id=?", (note["aweme_id"],))
        if existing:
            self.execute(
                f"UPDATE notes SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?",
                (*(note.get(f) for f in fields), now_iso(), existing["id"]),
            )
            return int(existing["id"])
        marks = ",".join("?" for _ in fields)
        now = now_iso()
        return self.execute(
            f"INSERT INTO notes(aweme_id,{','.join(fields)},collected_at,updated_at) VALUES(?,{marks},?,?)",
            (note["aweme_id"], *(note.get(f) for f in fields), now, now),
        )

    def link_job_note(self, job_id: int, note_id: int) -> None:
        self.execute("INSERT OR IGNORE INTO search_job_notes(job_id,note_id) VALUES(?,?)", (job_id, note_id))

    def list_job_notes(self, job_id: int) -> list[dict[str, Any]]:
        return self.fetchall(
            "SELECT n.* FROM notes n JOIN search_job_notes j ON j.note_id=n.id WHERE j.job_id=? ORDER BY n.id",
            (job_id,),
        )

    def count_job_notes(self, job_id: int) -> int:
        row = self.fetchone("SELECT COUNT(*) n FROM search_job_notes WHERE job_id=?", (job_id,))
        return int((row or {}).get("n", 0))
