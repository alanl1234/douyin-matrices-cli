"""Tests for the durable task queue (P0Store.task_queue + DurableTaskQueue)."""
from __future__ import annotations

import sqlite3

from dy_cli.dashboard.persistence import P0Store, QueueItem
from dy_cli.dashboard.queue import DurableTaskQueue, RunResult


def _reset_available(s: P0Store) -> None:
    """Force every non-terminal task to be claimable now (test helper)."""
    con = sqlite3.connect(s.path)
    con.execute(
        "UPDATE task_queue SET available_at='2000-01-01T00:00:00+00:00' "
        "WHERE status IN ('queued','retry_wait')"
    )
    con.commit()
    con.close()


def test_enqueue_is_idempotent(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    qid = s.enqueue("publish", 10, 1, max_attempts=2)
    assert qid > 0
    # re-enqueue same kind:resource_id while queued -> same id
    assert s.enqueue("publish", 10, 1) == qid
    counts = s.queue_counts()
    assert counts.get("queued") == 1


def test_claim_serializes_per_account(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    s.enqueue("publish", 1, account_id=7)
    s.enqueue("publish", 2, account_id=7)
    item = s.claim(lease_seconds=180)
    assert isinstance(item, QueueItem)
    assert item.attempts == 1
    # same account still "running" -> no second claim
    assert s.claim(180) is None


def test_heartbeat_updates_lease(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    s.enqueue("publish", 1, account_id=1)
    item = s.claim(180)
    s.heartbeat(item.id, 180)
    # after heartbeat the item is still running; claim returns None
    assert s.claim(180) is None


def test_finish_done_and_counts(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    s.enqueue("publish", 1, account_id=1)
    item = s.claim(180)
    s.finish(item, "done")
    assert s.queue_counts().get("done") == 1


def test_finish_retry_then_failed(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    s.enqueue("publish", 1, account_id=1, max_attempts=2)
    item = s.claim(180)
    # retryable and under max_attempts -> retry_wait
    s.finish(item, "failed", retryable=True)
    assert s.queue_counts().get("retry_wait") == 1
    # force claimable, retry fails again -> final failed
    _reset_available(s)
    item2 = s.claim(180)
    assert item2.attempts == 2
    s.finish(item2, "failed", retryable=True)
    assert s.queue_counts().get("failed") == 1


def test_recover_expired_publish_becomes_manual(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    s.enqueue("publish", 1, account_id=1)
    item = s.claim(180)
    # simulate crash: force lease into the past, then recover
    con = sqlite3.connect(s.path)
    con.execute("UPDATE task_queue SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (item.id,))
    con.commit()
    con.close()
    s.recover_expired()
    assert s.queue_counts().get("manual") == 1


def test_cancel(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    s.enqueue("publish", 1, account_id=1)
    s.cancel("publish", 1)
    assert s.queue_counts().get("cancelled") == 1
    assert s.claim(180) is None


def test_durable_queue_executes_runner(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    calls = []

    def runner(item):
        calls.append(item.resource_id)
        return RunResult("done")

    q = DurableTaskQueue(s, runner, workers=1, lease_seconds=180, poll_seconds=0.05)
    q.enqueue("publish", 42, account_id=1)
    # drive manually (no supervisor thread) to keep the test deterministic
    item = s.claim(180)
    assert item is not None
    q._execute(item)
    assert calls == [42]
    assert s.queue_counts().get("done") == 1


def test_durable_queue_retry_loop(tmp_path):
    s = P0Store(tmp_path / "p0.sqlite3")
    attempts = {"n": 0}

    def runner(item):
        attempts["n"] += 1
        # fail twice, then succeed
        if attempts["n"] < 3:
            return RunResult("failed", retryable=True)
        return RunResult("done")

    q = DurableTaskQueue(s, runner, workers=1, lease_seconds=180, poll_seconds=0.05)
    q.enqueue("publish", 1, account_id=1, max_attempts=3)
    for _ in range(4):
        _reset_available(s)
        item = s.claim(180)
        if item is None:
            break
        q._execute(item)
    assert attempts["n"] == 3
    assert s.queue_counts().get("done") == 1
