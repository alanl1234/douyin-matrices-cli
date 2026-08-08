"""Tests for the headless QR login session manager (网页内扫码绑定).

These run WITHOUT a real browser: the launcher is replaced with a fake that
returns stub browser/page objects, and the capture/poll helpers are patched.
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


class FakePw:
    def stop(self):
        return None


class FakePage:
    def __init__(self, logged_in=False):
        self.url = "https://login.douyin.com/" if not logged_in else "https://creator.douyin.com/creator-micro/content/manage"
        self._logged_in = logged_in

    def get_by_text(self, text, exact=False):
        # When logged in, the login modal texts are gone.
        class _Loc:
            def count(self_inner):
                return 0 if self._logged_in else 1

        return _Loc()


def _make_manager(logged_in=False):
    mgr = QrLoginManager()

    def fake_launcher(alias):
        return FakePw(), FakeBrowser(), FakeContext(), FakePage(logged_in=logged_in)

    mgr._launcher = fake_launcher
    return mgr


def test_start_returns_qr_data_url():
    mgr = _make_manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=b"PNGDATA"):
        res = mgr.start("ali", _cookie_path("ali"))
    assert res["qr_data_url"].startswith("data:image/png;base64,")
    assert len(res["session_id"]) == 32


def test_start_qr_not_captured_returns_error():
    mgr = _make_manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=None):
        res = mgr.start("ali", _cookie_path("ali"))
    assert res["qr_data_url"] is None
    assert "error" in res


def test_status_waiting_then_bound():
    mgr = _make_manager(logged_in=False)
    with mock.patch.object(qrl, "_capture_qr", return_value=b"PNGDATA"):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]

    with mock.patch.object(qrl, "_is_logged_in", return_value=False):
        st = mgr.status(sid)
    assert st["status"] in ("waiting", "scanning")

    with mock.patch.object(qrl, "_is_logged_in", return_value=True), \
         mock.patch.object(qrl, "_collect_cookies"), \
         mock.patch.object(qrl, "_safe_close"), \
         mock.patch.object(account_bridge, "register_or_update_account") as reg:
        st = mgr.status(sid)
    assert st["status"] == "bound"
    reg.assert_called_once()
    # session removed after bound
    assert mgr.status(sid)["status"] == "gone"


def test_status_expired_after_ttl():
    mgr = _make_manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=b"PNGDATA"):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    mgr._sessions[sid].created_at = time.time() - (qrl.SESSION_TTL_SECONDS + 5)
    with mock.patch.object(qrl, "_is_logged_in", return_value=False), \
         mock.patch.object(qrl, "_safe_close"):
        st = mgr.status(sid)
    assert st["status"] == "expired"


def test_status_unknown_session_gone():
    mgr = QrLoginManager()
    assert mgr.status("nope")["status"] == "gone"


def test_close_removes_session():
    mgr = _make_manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=b"PNGDATA"):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    mgr.close(sid)
    assert mgr.status(sid)["status"] == "gone"


def test_sweep_drops_expired():
    mgr = _make_manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=b"PNGDATA"):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    mgr._sessions[sid].created_at = time.time() - (qrl.SESSION_TTL_SECONDS + 5)
    removed = mgr.sweep()
    assert removed == 1
    assert mgr.status(sid)["status"] == "gone"
