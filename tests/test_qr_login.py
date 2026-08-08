"""Tests for the headless QR login session manager (网页内扫码绑定).

These run WITHOUT a real browser: the launcher is replaced with a fake that
returns stub browser/context/page objects, and ``_capture_qr`` / ``_is_logged_in``
are patched. The manager runs either synchronously (``run_in_thread=False``) for
deterministic assertions or in the background (default) where we stop it via
``close()`` / ``sweep()``.
"""
from __future__ import annotations

import os
import tempfile
import time
from unittest import mock

from dy_cli import account_bridge
from dy_cli.dashboard import qr_login as qrl
from dy_cli.dashboard.qr_login import QrLoginManager


def _cookie_path(name: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"dy_qr_test_{name}.json")


class FakeContext:
    def storage_state(self, path):
        return None


class FakeBrowser:
    def close(self):
        return None


class FakePage:
    def __init__(self, logged_in=False):
        self.url = (
            "https://login.douyin.com/"
            if not logged_in
            else "https://creator.douyin.com/creator-micro/content/manage"
        )
        self._logged_in = logged_in

    def get_by_text(self, text, exact=False):
        class _Loc:
            def count(self_inner):
                return 0 if self._logged_in else 1

        return _Loc()


def _manager(run_in_thread: bool = True):
    mgr = QrLoginManager(run_in_thread=run_in_thread)
    mgr._launcher = lambda alias: (FakeBrowser(), FakeContext(), FakePage())
    return mgr


def test_start_returns_session_and_qr_url():
    mgr = _manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=True):
        res = mgr.start("ali", _cookie_path("ali"))
    assert "session_id" in res and len(res["session_id"]) == 32
    assert res["qr_image_url"].startswith("/api/accounts/qr-image")
    mgr.close(res["session_id"])


def test_start_qr_not_captured_sets_error():
    # Synchronous run so we can observe the error status deterministically.
    mgr = QrLoginManager(run_in_thread=False)
    mgr._launcher = lambda alias: (FakeBrowser(), FakeContext(), FakePage())
    with mock.patch.object(qrl, "_capture_qr", return_value=False):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    st = mgr.status(sid)
    assert st["status"] == "error"
    assert st["error"]


def test_status_bound_and_removed():
    # Synchronous run: QR captured, logged-in immediately -> bound -> session dropped.
    mgr = QrLoginManager(run_in_thread=False)
    mgr._launcher = lambda alias: (FakeBrowser(), FakeContext(), FakePage())
    with mock.patch.object(qrl, "_capture_qr", return_value=True), \
         mock.patch.object(qrl, "_is_logged_in", return_value=True), \
         mock.patch.object(account_bridge, "register_or_update_account") as reg:
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    # bound sessions are popped in finally -> gone
    assert mgr.status(sid)["status"] == "gone"
    reg.assert_called_once()


def test_status_waiting_until_closed():
    mgr = _manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=True), \
         mock.patch.object(qrl, "_is_logged_in", return_value=False):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    # wait until the loop has set a live status (not "starting")
    for _ in range(60):
        st = mgr.status(sid)["status"]
        if st != "starting":
            break
        time.sleep(0.05)
    assert mgr.status(sid)["status"] in ("waiting", "scanning")
    mgr.close(sid)


def test_status_expired_via_sweep():
    mgr = _manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=True):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    mgr._sessions[sid].created_at = time.time() - (qrl.SESSION_TTL_SECONDS + 5)
    removed = mgr.sweep()
    assert removed == 1
    assert mgr.status(sid)["status"] == "gone"


def test_status_unknown_session_gone():
    mgr = QrLoginManager()
    assert mgr.status("nope")["status"] == "gone"
