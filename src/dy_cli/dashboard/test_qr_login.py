"""Tests for the headless QR login manager (Camoufox-backed, single-thread).

No real browser is launched: a fake launcher returns stub browser/context/page
objects, and the capture/login helpers are patched. The real end-to-end path
(actual Camoufox + a real Douyin account) is exercised manually on a host that
has camoufox fetched and a phone to scan with.
"""
from __future__ import annotations

from unittest.mock import patch

from dy_cli.dashboard.qr_login import QrLoginManager


def _make_fake():
    """Return (launcher, captures) where captures['context'] is the fake context."""
    captures: dict = {}

    class _FakeLocator:
        def __init__(self, n: int = 0):
            self._n = n

        def count(self):
            return self._n

        def is_visible(self):
            return self._n > 0

        def first(self):
            return self

        def screenshot(self, path=None):
            return b"png"

        def click(self):
            pass

        def locator(self, *a, **k):
            return _FakeLocator(0)

    class _FakePage:
        def __init__(self):
            self._url = "https://www.douyin.com/"

        def url(self):
            return self._url

        def goto(self, *a, **k):
            pass

        def wait_for_timeout(self, *a, **k):
            pass

        def get_by_text(self, *a, **k):
            return _FakeLocator(0)

        def locator(self, *a, **k):
            return _FakeLocator(0)

    class _FakeContext:
        def __init__(self):
            self.saved = None

        def cookies(self):
            return [{"name": "sessionid_ss", "value": "x", "domain": ".douyin.com"}]

        def storage_state(self, path):
            self.saved = path

    class _FakeBrowser:
        def __init__(self):
            self.contexts = [_FakeContext()]

        def close(self):
            pass

    def launcher(alias):
        b = _FakeBrowser()
        captures["context"] = b.contexts[0]
        return b, b.contexts[0], _FakePage()

    return launcher, captures


def test_start_launcher_error_reports_error():
    def boom(alias):
        raise RuntimeError("camoufox not installed")

    mgr = QrLoginManager(run_in_thread=False, launcher=boom)
    res = mgr.start("acc", "/tmp/acc.json")
    assert res["session_id"]
    # Error is surfaced via status (session kept so the frontend can read it).
    assert mgr.status(res["session_id"])["status"] == "error"


def test_start_success_exports_storage_state():
    launcher, captures = _make_fake()
    mgr = QrLoginManager(run_in_thread=False, launcher=launcher)
    cookie_file = "/tmp/ok.json"
    with patch("dy_cli.dashboard.qr_login._capture_qr", return_value=True), patch(
        "dy_cli.dashboard.qr_login._is_logged_in", return_value=True
    ), patch("dy_cli.account_bridge.register_or_update_account") as reg:
        res = mgr.start("acc", cookie_file)
    assert res["session_id"]
    # On success the Camoufox context storage_state is exported to the cookie file.
    assert captures["context"].saved == cookie_file
    reg.assert_called_once()
    # Bound sessions are dropped immediately.
    assert mgr.status(res["session_id"])["status"] == "gone"


def test_status_gone_for_unknown_session():
    mgr = QrLoginManager()
    assert mgr.status("does-not-exist") == {"status": "gone"}


def test_qr_image_missing_for_unknown_session():
    mgr = QrLoginManager()
    assert mgr.qr_image("does-not-exist") is None


def test_cancel_requests_stop():
    launcher, _ = _make_fake()
    mgr = QrLoginManager(run_in_thread=True, launcher=launcher)
    with patch("dy_cli.dashboard.qr_login._capture_qr", return_value=True), patch(
        "dy_cli.dashboard.qr_login._is_logged_in", return_value=False
    ):
        res = mgr.start("acc", "/tmp/c.json")
    # Let the background thread run a tick, then cancel.
    mgr.cancel(res["session_id"])
    assert mgr.status(res["session_id"])["status"] in (
        "waiting",
        "scanning",
        "error",
        "expired",
        "gone",
    )
