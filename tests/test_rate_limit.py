"""Tests for the per-account rate limiter + persistent store."""
from __future__ import annotations

from pathlib import Path

from dy_cli.dashboard.persistence import P0Store
from dy_cli.dashboard.rate_limit import AccountRateLimiter


def _store(tmp_path: Path) -> P0Store:
    return P0Store(tmp_path / "rate.sqlite3")


def test_first_request_allowed(tmp_path):
    store = _store(tmp_path)
    wait = store.acquire_request(1, interval_seconds=1.0, daily_limit=10)
    assert wait == 0.0
    # daily count incremented
    wait2 = store.acquire_request(1, interval_seconds=1.0, daily_limit=10)
    # second immediate call must wait for the per-request interval
    assert wait2 > 0


def test_daily_limit(tmp_path):
    store = _store(tmp_path)
    store.acquire_request(1, interval_seconds=0.0, daily_limit=1)
    wait = store.acquire_request(1, interval_seconds=0.0, daily_limit=1)
    # after hitting the daily cap, must wait until next midnight (large value)
    assert wait > 3600


def test_pause_blocks(tmp_path):
    store = _store(tmp_path)
    store.pause_account(1, seconds=120, reason="manual")
    wait = store.acquire_request(1, interval_seconds=0.0, daily_limit=100)
    assert 0 < wait <= 120


def test_limiter_acquire_under_limit(tmp_path):
    limiter = AccountRateLimiter(
        _store(tmp_path), interval_seconds=0.0, daily_limit=100
    )
    # should not raise / block meaningfully with zero interval
    limiter.acquire(1)


def test_limiter_pause(tmp_path):
    limiter = AccountRateLimiter(
        _store(tmp_path), interval_seconds=0.0, daily_limit=100
    )
    limiter.pause(1, reason="test", seconds=60)
    wait = limiter.store.acquire_request(1, 0.0, 100)
    assert wait > 0
