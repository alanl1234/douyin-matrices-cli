"""Headless QR-code login sessions for the dashboard (网页内扫码绑定).

The dashboard backend launches a *headless* Playwright browser, navigates to the
Douyin creator login page, captures the QR image, and serves it to the web page.
The user scans with the Douyin App; the backend polls the headless page for login
completion, then persists ``storage_state`` to the account's cookie file and
registers the account in the matrix DB.

This reuses the QR / login page constants from
``dy_cli.engines.playwright_client`` so the same capture & poll logic can also be
driven from the CLI. Playwright itself is imported lazily so importing this module
never requires the browser runtime (keeps CI import checks clean).
"""
from __future__ import annotations

import base64
import os
import threading
import time
import uuid
from typing import Any, Callable

# Default QR session lifetime. Douyin QR codes expire in ~2 minutes; we give a
# little headroom and then report the session as expired.
SESSION_TTL_SECONDS = 180


def _safe_close(pw: Any, browser: Any) -> None:
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass
    try:
        if pw is not None:
            pw.stop()
    except Exception:
        pass


def _launch_qr_browser(alias: str):
    """Launch a headless browser and open the Douyin login page.

    Returns ``(pw, browser, context, page)``. Playwright's *sync* API is used so
    the page handle can be polled from synchronous FastAPI handlers without an
    event loop. Imported lazily.
    """
    from playwright.sync_api import sync_playwright

    from ..engines.playwright_client import PlaywrightClient

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    )
    page = context.new_page()
    page.goto(PlaywrightClient.CREATOR_URL, wait_until="domcontentloaded")
    # Let the login QR render.
    page.wait_for_timeout(2000)
    return pw, browser, context, page


def _capture_qr(page: Any) -> bytes | None:
    """Extract the Douyin login QR as PNG bytes, or None if not found.

    Douyin renders the QR on a ``<canvas>`` (or an ``<img>``). We try a few
    candidate selectors, then fall back to screenshotting the element that
    contains the "扫码登录" prompt.
    """
    candidates = [
        "canvas",
        'img[src*="qrcode"]',
        'img[src*="qr"]',
        '[class*="qrcode"] img',
        '[class*="qr-code"] img',
        '[class*="scan"] canvas',
        '[class*="login"] canvas',
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                return el.screenshot()
        except Exception:
            continue
    # Fallback: the container around the 扫码登录 text.
    try:
        anchor = page.get_by_text("扫码登录", exact=False).first
        if anchor.count() > 0:
            container = anchor.locator("..")
            if container.count() > 0:
                return container.screenshot()
    except Exception:
        pass
    return None


def _is_logged_in(page: Any) -> bool:
    """True once the headless page reached the logged-in creator dashboard."""
    try:
        url = page.url
    except Exception:
        return False
    if "creator-micro" in url:
        return True
    # Login modal dismissed and we are no longer on a passport/login host.
    try:
        if (
            page.get_by_text("扫码登录", exact=False).count() == 0
            and page.get_by_text("手机号登录", exact=False).count() == 0
            and "passport" not in url
            and "login" not in url
        ):
            return True
    except Exception:
        pass
    return False


def _is_qr_scanned(page: Any) -> bool:
    """Best-effort: detect that the phone already scanned (pending confirm)."""
    try:
        for hint in ("扫描成功", "已扫描", "请在手机上确认"):
            if page.get_by_text(hint, exact=False).count() > 0:
                return True
    except Exception:
        pass
    return False


def _collect_cookies(page: Any) -> None:
    """Visit a couple of pages so the full cookie set is captured (mirrors CLI)."""
    for url in [
        "https://www.douyin.com/",
        "https://creator.douyin.com/creator-micro/content/manage",
    ]:
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
        except Exception:
            pass


class QrSession:
    """A live headless login session held in the dashboard process."""

    def __init__(self, session_id: str, alias: str, pw: Any, browser: Any, context: Any, page: Any, cookie_file: str):
        self.session_id = session_id
        self.alias = alias
        self.pw = pw
        self.browser = browser
        self.context = context
        self.page = page
        self.cookie_file = cookie_file
        self.created_at = time.time()
        self.status = "waiting"  # waiting -> scanning -> bound -> error/expired
        self.error: str | None = None
        self.qr_b64: str | None = None
        self.lock = threading.Lock()


class QrLoginManager:
    """In-memory registry of headless QR login sessions.

    Testable without a real browser: replace ``_launcher`` with a fake that
    returns ``(pw, browser, context, page)`` stubs, and patch the module-level
    ``_capture_qr`` / ``_is_logged_in`` helpers.
    """

    def __init__(self, sessions: dict[str, QrSession] | None = None, launcher: Callable[[str], Any] | None = None):
        self._sessions: dict[str, QrSession] = sessions if sessions is not None else {}
        self._lock = threading.Lock()
        self._launcher: Callable[[str], Any] = launcher or _launch_qr_browser

    def start(self, alias: str, cookie_file: str) -> dict[str, Any]:
        """Launch a headless QR session; return {session_id, qr_data_url, error}."""
        session_id = uuid.uuid4().hex
        try:
            pw, browser, context, page = self._launcher(alias)
        except Exception as e:  # e.g. Playwright not installed
            return {"session_id": session_id, "qr_data_url": None, "error": f"无法启动浏览器: {e}"}

        qr_bytes = _capture_qr(page)
        if qr_bytes is None:
            _safe_close(pw, browser)
            return {
                "session_id": session_id,
                "qr_data_url": None,
                "error": "未能捕获二维码（抖音可能启用了反爬校验，请改用 CLI: dy account add）",
            }

        session = QrSession(session_id, alias, pw, browser, context, page, cookie_file)
        session.qr_b64 = base64.b64encode(qr_bytes).decode("ascii")
        with self._lock:
            self._sessions[session_id] = session
        return {"session_id": session_id, "qr_data_url": f"data:image/png;base64,{session.qr_b64}"}

    def status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return {"status": "gone"}

        with session.lock:
            if session.status in ("bound", "error", "expired"):
                return {"status": session.status, "error": session.error}

            # TTL expiry.
            if time.time() - session.created_at > SESSION_TTL_SECONDS:
                session.status = "expired"
                _safe_close(session.pw, session.browser)
                with self._lock:
                    self._sessions.pop(session_id, None)
                return {"status": "expired"}

            try:
                if _is_logged_in(session.page):
                    _collect_cookies(session.page)
                    os.makedirs(os.path.dirname(session.cookie_file), exist_ok=True)
                    session.context.storage_state(path=session.cookie_file)
                    from ..account_bridge import register_or_update_account

                    register_or_update_account(
                        session.alias,
                        cookie_file=session.cookie_file,
                        login_status="ready",
                    )
                    session.status = "bound"
                    _safe_close(session.pw, session.browser)
                    with self._lock:
                        self._sessions.pop(session_id, None)
                    return {"status": "bound"}
                session.status = "scanning" if _is_qr_scanned(session.page) else "waiting"
                return {"status": session.status}
            except Exception as e:
                session.status = "error"
                session.error = str(e)
                _safe_close(session.pw, session.browser)
                with self._lock:
                    self._sessions.pop(session_id, None)
                return {"status": "error", "error": str(e)}

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            _safe_close(session.pw, session.browser)

    def sweep(self) -> int:
        """Drop expired sessions; returns number removed."""
        removed = 0
        with self._lock:
            expired = [
                sid
                for sid, s in self._sessions.items()
                if time.time() - s.created_at > SESSION_TTL_SECONDS
            ]
            for sid in expired:
                s = self._sessions.pop(sid)
                _safe_close(s.pw, s.browser)
                removed += 1
        return removed
