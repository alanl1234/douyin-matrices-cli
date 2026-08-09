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
    with mock.patch.object(qrl, "_capture_qr", return_value=True), \
         mock.patch.object(qrl, "_is_qr_expired", return_value=False):
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
         mock.patch.object(qrl, "_is_qr_expired", return_value=False), \
         mock.patch.object(account_bridge, "register_or_update_account") as reg:
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    # bound sessions are popped in finally -> gone
    assert mgr.status(sid)["status"] == "gone"
    reg.assert_called_once()


def test_status_waiting_until_closed():
    mgr = _manager()
    with mock.patch.object(qrl, "_capture_qr", return_value=True), \
         mock.patch.object(qrl, "_is_logged_in", return_value=False), \
         mock.patch.object(qrl, "_is_qr_expired", return_value=False):
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


def test_qr_refresh_on_expiry():
    # When the QR is detected as expired, the manager must refresh + re-capture
    # (so the served PNG stays scannable) instead of silently timing out.
    mgr = _manager()  # runs in a background thread
    refresh_calls = []
    state = {"n": 0}

    def fake_expired(*_a, **_k):
        state["n"] += 1
        return state["n"] <= 1  # expired on the first check, then fresh

    with mock.patch.object(qrl, "_capture_qr", return_value=True), \
         mock.patch.object(qrl, "_is_logged_in", return_value=False), \
         mock.patch.object(qrl, "_is_qr_expired", side_effect=fake_expired), \
         mock.patch.object(qrl, "_is_qr_scanned", return_value=False), \
         mock.patch.object(qrl, "_refresh_qr", side_effect=lambda p: refresh_calls.append(1)):
        res = mgr.start("ali", _cookie_path("ali"))
    sid = res["session_id"]
    for _ in range(80):
        if refresh_calls:
            break
        time.sleep(0.05)
    mgr.close(sid)
    assert refresh_calls, "expected at least one QR refresh on expiry"


def test_is_logged_in_ignores_homepage_url():
    """未登录首页 URL 含 douyin.com，绝不能误判为已登录（否则无操作就 bound）。

    复现并锁定此前线上 bug：start() 打开 www.douyin.com 首页，未登录时其 URL
    仍是 ``www.douyin.com``，若用 ``"douyin.com" in url`` 判定登录，会瞬间 bound
    —— 表现为「无操作显示绑定成功」且二维码图片因会话被立即删除而加载失败。
    """
    class CtxNoLogin:
        def cookies(self):
            return [{"name": "ttwid", "value": "x"}]  # 非登录态 cookie

    class CtxLoggedIn:
        def cookies(self):
            return [{"name": "sessionid_ss", "value": "x"}]  # 抖音登录态 cookie

    class HomePage:
        url = "https://www.douyin.com/"  # 未登录首页

    assert qrl._is_logged_in(HomePage(), CtxNoLogin()) is False
    assert qrl._is_logged_in(HomePage(), CtxLoggedIn()) is True


def test_capture_qr_returns_false_on_empty_page_and_dumps_diagnostics(tmp_path, monkeypatch):
    """当页面上找不到任何二维码元素时，捕获应返回 False，并把诊断落盘。"""
    import dy_cli.dashboard.qr_login as qrl

    monkeypatch.setattr(qrl, "QR_DEBUG_ROOT", str(tmp_path))

    class _Loc:
        def count(self):
            return 0

        def is_visible(self):
            return False

        def bounding_box(self):
            return None

        def screenshot(self, path):
            pass

        def locator(self, sel):
            return _Loc()

    class _Page:
        frames = []

        def locator(self, sel):
            return _Loc()

        def get_by_text(self, *a, **k):
            return _Loc()

        def content(self):
            return "<html><body>no-qr</body></html>"

        def screenshot(self, path, full_page=False):
            pass

    png = str(tmp_path / "qr.png")
    assert qrl._capture_qr(_Page(), png) is False
    qrl._dump_qr_diagnostics(_Page(), png)
    assert (tmp_path / "last.txt").exists()
    assert list(tmp_path.glob("qr_dump_*.html"))
