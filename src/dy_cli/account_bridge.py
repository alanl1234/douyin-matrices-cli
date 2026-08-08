"""Bridge between the matrix dashboard (multi-account) and the dy CLI clients.

The dashboard keeps a SQLite account matrix (alias / douyin_user_id / persona /
group). Cookie files are stored by the existing dy machinery under
``~/.dy/cookies/<alias>.json`` (Playwright storage_state). This module resolves a
user-supplied ``--account <id|alias|douyin_user_id>`` to an *alias* that the
existing ``DouyinAPIClient.from_config(alias)`` and
``PlaywrightClient(account=alias)`` already understand — so the matrix layer never
writes the global cookie file and never touches the underlying clients.

Design goal (mirrors xiaohongshu-matrices-cli's account_bridge): given an
identifier, return the right per-account cookie, with graceful degradation when
the dashboard is not yet initialised.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import config as dy_config

# Reuse the legacy cookie location so existing clients keep working unchanged.
LEGACY_COOKIES_DIR = dy_config.COOKIES_DIR


def legacy_cookie_file(name: str) -> str:
    return os.path.join(LEGACY_COOKIES_DIR, f"{name}.json")


def _load_db() -> Any | None:
    """Best-effort open of the matrix DB; None if the dashboard is unused."""
    try:
        from .dashboard.config import DashboardConfig
        from .dashboard.db import Database
    except Exception:
        return None
    try:
        cfg = DashboardConfig.load()
    except Exception:
        return None
    # Guard: do not *create* a dashboard just because --account was passed.
    if not cfg.database_path.exists():
        return None
    try:
        return Database(cfg.database_path)
    except Exception:
        return None


def resolve_account_row(identifier: str | int | None) -> dict[str, Any] | None:
    """Find an account row by int id, alias, or douyin_user_id, or legacy file."""
    if identifier is None:
        return None
    db = _load_db()
    if db is not None:
        row = db.resolve_account(identifier)
        if row:
            return row
    # Legacy fallback: ~/.dy/cookies/<identifier>.json
    legacy = legacy_cookie_file(str(identifier))
    if os.path.isfile(legacy):
        return {
            "id": None,
            "alias": str(identifier),
            "douyin_user_id": "",
            "nickname": "",
            "login_status": "legacy",
            "persona_id": None,
            "group_name": "",
            "enabled": 1,
            "cookie_file": legacy,
        }
    return None


def resolve_alias(identifier: str | int | None) -> str | None:
    """Return the account alias for the existing clients, or None (use default)."""
    row = resolve_account_row(identifier)
    return row["alias"] if row else None


def resolve_cookie_file(identifier: str | int | None) -> str | None:
    """Return the absolute cookie (storage_state) file path for an account."""
    row = resolve_account_row(identifier)
    if not row:
        return None
    cf = row.get("cookie_file") or ""
    if cf and os.path.isfile(cf):
        return cf
    return legacy_cookie_file(row["alias"])


def list_matrix_accounts() -> list[dict[str, Any]]:
    """Read-only listing of matrix accounts (for `dy status`). Empty if unused."""
    db = _load_db()
    if db is None:
        return []
    rows = db.list_accounts()
    out: list[dict[str, Any]] = []
    for r in rows:
        cf = r.get("cookie_file") or legacy_cookie_file(r.get("alias", ""))
        out.append(
            {
                "id": r.get("id"),
                "alias": r.get("alias"),
                "douyin_user_id": r.get("douyin_user_id"),
                "nickname": r.get("nickname"),
                "login_status": r.get("login_status"),
                "persona_id": r.get("persona_id"),
                "group_name": r.get("group_name"),
                "enabled": r.get("enabled"),
                "has_cookie_file": os.path.isfile(cf),
            }
        )
    return out


def resolve_persona(alias: str | None) -> dict[str, Any] | None:
    """Return the persona bound to an account (topics / forbidden words), if any."""
    if not alias:
        return None
    db = _load_db()
    if db is None:
        return None
    row = db.get_account_by_alias(alias)
    if not row or not row.get("persona_id"):
        return None
    return db.get_persona(int(row["persona_id"]))


def register_or_update_account(
    alias: str,
    *,
    cookie_file: str | None = None,
    douyin_user_id: str = "",
    nickname: str = "",
    login_status: str = "ready",
    persona_id: int | None = None,
    group_name: str = "",
) -> int:
    """Insert or update a matrix account row after login.

    Returns the account id. Creates the dashboard DB on first use.
    """
    from .dashboard.config import DashboardConfig
    from .dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    existing = db.get_account_by_alias(alias)
    cf = cookie_file or legacy_cookie_file(alias)
    if existing:
        db.update(
            "accounts",
            int(existing["id"]),
            cookie_file=cf,
            douyin_user_id=douyin_user_id,
            nickname=nickname,
            login_status=login_status,
            group_name=group_name,
        )
        if persona_id is not None:
            db.update("accounts", int(existing["id"]), persona_id=persona_id)
        return int(existing["id"])
    return db.create_account(
        alias=alias,
        cookie_file=cf,
        douyin_user_id=douyin_user_id,
        nickname=nickname,
        login_status=login_status,
        persona_id=persona_id,
        group_name=group_name,
    )


def effective_account(ctx: Any) -> str | None:
    """Resolve the requested account from click context into an alias (or None)."""
    if ctx is None or not getattr(ctx, "obj", None):
        return None
    raw = ctx.obj.get("account")
    return resolve_alias(raw) if raw else None


# ── session / token cache (对齐 xhs 的 token 缓存持久化)──────────────────────
def _cookies_dir() -> Path:
    try:
        from .dashboard.config import DashboardConfig

        return DashboardConfig.load().cookies_dir
    except Exception:
        return Path(LEGACY_COOKIES_DIR)


def cache_session(alias: str, data: dict[str, Any]) -> None:
    """Persist a resolved session (cookies / token) for fast reuse.

    Mirrors xhs's token cache: avoids re-decrypting the profile on every CLI call.
    """
    path = _cookies_dir() / f"{alias}.session.json"
    payload = {"alias": alias, "cached_at": datetime.now(timezone.utc).isoformat(), "data": data}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_cached_session(alias: str, max_age_seconds: int = 300) -> dict[str, Any] | None:
    """Return cached session data if present and younger than ``max_age_seconds``."""
    path = _cookies_dir() / f"{alias}.session.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    cached_at = datetime.fromisoformat(payload.get("cached_at", "1970-01-01T00:00:00+00:00"))
    age = (datetime.now(timezone.utc) - cached_at).total_seconds()
    if age > max_age_seconds:
        return None
    return payload.get("data")


# ── cookie / health verification (对齐 xhs 的 verify 路由)────────────────────
def verify_account_cookies(alias: str) -> tuple[bool, str]:
    """Best-effort check that an account's storage_state cookie is present & non-empty."""
    cf = resolve_cookie_file(alias)
    if not cf:
        return False, "未找到 cookie 文件（请先登录）"
    if not os.path.isfile(cf):
        return False, f"cookie 文件不存在: {cf}"
    try:
        size = os.path.getsize(cf)
    except OSError:
        return False, f"无法读取 cookie 文件: {cf}"
    if size < 64:
        return False, "cookie 文件过小，可能登录态已失效"
    return True, "ok"


# ── profile lock (对齐 xhs 的 active/stale profile lock)──────────────────────
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack signal permission — it is alive.
        return True
    except OSError:
        # Windows: a missing pid raises OSError (e.g. winerror 87), not
        # ProcessLookupError — treat any other OSError as "not found".
        return False


def _lock_path(alias: str) -> Path:
    return _cookies_dir() / f"{alias}.lock.json"


def acquire_profile_lock(alias: str, owner: str | None = None, ttl_seconds: int = 3600) -> bool:
    """Acquire an exclusive lock so two processes never drive the same account.

    A lock held by a *live* process (and not expired) blocks; a stale lock
    (dead pid or past ttl) is reclaimed. Returns True on success.
    """
    path = _lock_path(alias)
    now = time.time()
    if path.is_file():
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cur = None
        if cur:
            holder_pid = int(cur.get("pid", 0))
            acquired = float(cur.get("acquired_at", 0))
            # Reentrant: the current process already holds it.
            if holder_pid == os.getpid():
                return True
            if holder_pid and _pid_alive(holder_pid) and (now - acquired) < ttl_seconds:
                return False  # actively held by another process
    payload = {
        "pid": os.getpid(),
        "owner": owner or f"pid-{os.getpid()}",
        "acquired_at": now,
        "ttl_seconds": ttl_seconds,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return True


def release_profile_lock(alias: str) -> None:
    path = _lock_path(alias)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def profile_lock_holder(alias: str) -> dict[str, Any] | None:
    path = _lock_path(alias)
    if not path.is_file():
        return None
    try:
        cur = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    now = time.time()
    if not _pid_alive(int(cur.get("pid", 0))) or (now - float(cur.get("acquired_at", 0))) >= int(
        cur.get("ttl_seconds", 3600)
    ):
        return None  # stale
    return cur
