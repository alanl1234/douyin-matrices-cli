"""Tests for account-management parity with xiaohongshu-matrices-cli.

Covers: account health/verify (db), cookie verification, session cache, and the
profile lock (active vs stale) in ``account_bridge``.
"""
from __future__ import annotations

import json
import time

from dy_cli import account_bridge as ab
from dy_cli.dashboard.config import DashboardConfig
from dy_cli.dashboard.db import Database


def _db() -> Database:
    cfg = DashboardConfig.load()
    return Database(cfg.database_path)


def _write_cookie(path, size=200):
    path.write_text("{" + '"a":"b"' * (size // 9) + "}", encoding="utf-8")


def test_mark_verified_sets_last_verified():
    db = _db()
    aid = db.create_account(alias="v1", cookie_file="v1.json")
    assert db.get_account(aid)["last_verified_at"] is None
    db.mark_verified(aid)
    assert db.get_account(aid)["last_verified_at"]


def test_account_health_healthy_vs_unhealthy():
    db = _db()
    cookie = DashboardConfig.load().cookies_dir / "good.json"
    _write_cookie(cookie)
    aid = db.create_account(alias="good", cookie_file=str(cookie), login_status="ready")
    h = db.get_account_health(aid)
    assert h["healthy"] is True
    assert h["has_cookie_file"] is True

    bad = db.create_account(alias="bad", cookie_file="/no/such.json", login_status="unbound")
    hb = db.get_account_health(bad)
    assert hb["healthy"] is False


def test_verify_account_cookies():
    db = _db()
    cookie = DashboardConfig.load().cookies_dir / "chk.json"
    _write_cookie(cookie)
    aid = db.create_account(alias="chk", cookie_file=str(cookie))
    ok, reason = ab.verify_account_cookies("chk")
    assert ok is True and reason == "ok"
    db.mark_verified(aid)
    assert db.get_account(aid)["last_verified_at"]

    # missing file -> not ok, and db status flips to unbound on verify route
    missing = db.create_account(alias="miss", cookie_file="/no/such.json")
    ok2, _ = ab.verify_account_cookies("miss")
    assert ok2 is False


def test_session_cache_roundtrip_and_expiry():
    ab.cache_session("s1", {"token": "abc"})
    assert ab.load_cached_session("s1", max_age_seconds=300) == {"token": "abc"}
    # expired
    old = DashboardConfig.load().cookies_dir / "s1.session.json"
    payload = old.read_text(encoding="utf-8")
    p = json.loads(payload)
    import datetime as _dt

    p["cached_at"] = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=999)).isoformat()
    old.write_text(json.dumps(p), encoding="utf-8")
    assert ab.load_cached_session("s1", max_age_seconds=300) is None


def test_profile_lock_acquire_release_and_stale_reclaim():
    # first acquire succeeds (reentrant for same pid)
    assert ab.acquire_profile_lock("lk") is True
    assert ab.acquire_profile_lock("lk") is True  # same process re-acquire
    holder = ab.profile_lock_holder("lk")
    assert holder is not None and int(holder["pid"]) == __import__("os").getpid()

    # stale lock (dead pid / far past ttl) is reclaimed by a fresh acquire
    lock = ab._lock_path("stale")
    lock.write_text(
        json.dumps({"pid": 999999, "owner": "ghost", "acquired_at": time.time() - 10, "ttl_seconds": 3600}),
        encoding="utf-8",
    )
    assert ab.profile_lock_holder("stale") is None
    assert ab.acquire_profile_lock("stale") is True

    ab.release_profile_lock("lk")
    assert ab.profile_lock_holder("lk") is None
