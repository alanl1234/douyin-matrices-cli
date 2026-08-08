"""SQLite-backed durable task queue (对齐 xiaohongshu-matrices-cli DurableTaskQueue).

Adds leases, heartbeat, safe recovery after a crash, and idempotent enqueue on
top of :class:`P0Store`. The queue is generic: a ``runner`` callable performs the
actual work for a :class:`QueueItem` and returns a :class:`RunResult`. This is
what lets the orchestrator survive process restarts without losing publish /
search / engagement tasks.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .persistence import P0Store, QueueItem


@dataclass(frozen=True)
class RunResult:
    """Terminal outcome returned by a queue ``runner``."""

    status: str  # done | manual | cancelled | failed
    retryable: bool = False
    error: str | None = None


# Runner contract: given a QueueItem, do the work and return its terminal status.
Runner = Callable[[QueueItem], RunResult]


class DurableTaskQueue:
    def __init__(
        self,
        store: P0Store,
        runner: Runner,
        *,
        workers: int = 2,
        lease_seconds: int = 180,
        poll_seconds: float = 0.5,
    ) -> None:
        self.store = store
        self.runner = runner
        self.workers = max(1, workers)
        self.lease_seconds = max(60, lease_seconds)
        self.poll_seconds = max(0.1, poll_seconds)
        self._stop = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._futures: set[Future[None]] = set()

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self) -> None:
        if self._supervisor and self._supervisor.is_alive():
            return
        self.store.recover_expired()
        self._stop.clear()
        self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="dy-durable")
        self._supervisor = threading.Thread(target=self._loop, name="dy-queue-supervisor", daemon=True)
        self._supervisor.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._supervisor:
            self._supervisor.join(timeout=timeout)
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=False)

    def enqueue(self, kind: str, resource_id: int, account_id: int | None, *, max_attempts: int = 2) -> int:
        return self.store.enqueue(kind, resource_id, account_id, max_attempts=max_attempts)

    def cancel(self, kind: str, resource_id: int) -> None:
        self.store.cancel(kind, resource_id)

    def counts(self) -> dict[str, int]:
        return self.store.queue_counts()

    # ── supervisor loop ─────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._futures = {f for f in self._futures if not f.done()}
            while len(self._futures) < self.workers and not self._stop.is_set():
                item = self.store.claim(self.lease_seconds)
                if not item:
                    break
                if not self._pool:
                    return
                self._futures.add(self._pool.submit(self._execute, item))
            self._stop.wait(self.poll_seconds)

    def _heartbeat(self, item: QueueItem, stopped: threading.Event) -> None:
        interval = max(10.0, self.lease_seconds / 3)
        while not stopped.wait(interval):
            self.store.heartbeat(item.id, self.lease_seconds)

    def _execute(self, item: QueueItem) -> None:
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(item, heartbeat_stop), daemon=True)
        heartbeat.start()
        try:
            result = self.runner(item)
        except Exception as exc:  # never let a runner crash the supervisor
            result = RunResult("failed", retryable=item.kind == "search", error=str(exc)[:500])
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)
        self.store.finish(item, result.status, result.error, retryable=result.retryable)
