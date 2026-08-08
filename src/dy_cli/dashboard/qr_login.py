"""Headless QR-code login for the dashboard, powered by Camoufox.

Why Camoufox: Douyin aggressively fingerprints bare headless Chromium
(``navigator.webdriver`` exposed, no stealth) and frequently refuses to render
the QR or drops the session. Camoufox is a Firefox-based anti-detection Playwright
wrapper (humanize fingerprint randomization) and is used *only* for web QR login;
publishing/scraping still use the existing Playwright async client unchanged.

Architecture fix (the old code polled a sync Playwright ``page`` from different
FastAPI thread-pool threads, which crashed and was swallowed as "error"):
- ``start()`` launches Camoufox inside ONE dedicated background thread that drives
  the whole login poll loop; the page handle never crosses threads.
- Main-thread ``status()`` / ``qr_image()`` / ``cancel()`` only read a thread-safe
  state object or the QR PNG file; they never touch the browser.
- On success, ``context.storage_state()`` exports a Playwright-compatible
  storage_state JSON straight to ``~/.dy/cookies/<alias>.json``, so the existing
  ``PlaywrightClient`` reuses it with zero changes (cookie bridge done here).
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable

# Douyin QR codes live ~2 minutes; give headroom before reporting expired.
SESSION_TTL_SECONDS = 180

PROFILES_ROOT = os.path.join(os.path.expanduser("~"), ".dy", "camoufox_profiles")
QR_IMAGES_ROOT = os.path.join(os.path.expanduser("~"), ".dy", "qr_images")

# Any of these cookie names appearing means a logged-in session has landed.
LOGIN_COOKIE_KEYS = ("sessionid_ss", "sid_tt", "sid_guard", "sessionid")


def _ensure_dirs() -> None:
    os.makedirs(PROFILES_ROOT, exist_ok=True)
    os.makedirs(QR_IMAGES_ROOT, exist_ok=True)


def _launch_qr_browser(alias: str):
    """Launch Camoufox (persistent_context + anti-detect); return (browser, context, page).

    Imports camoufox lazily so this module imports cleanly without the browser
    runtime (keeps CI import checks green). Falls back to ``humanize=False`` if the
    humanize fingerprint data is not fetched yet, so login still works (weaker
    stealth, but not a hard failure).
    """
    from camoufox.sync_api import Camoufox

    profile_dir = os.path.join(PROFILES_ROOT, alias)
    os.makedirs(profile_dir, exist_ok=True)
    try:
        browser = Camoufox(
            headless=True,
            persistent_context=True,
            user_data_dir=profile_dir,
            humanize=True,
            locale="zh-CN",
        )
    except Exception:
        browser = Camoufox(
            headless=True,
            persistent_context=True,
            user_data_dir=profile_dir,
            humanize=False,
            locale="zh-CN",
        )
    context = browser.contexts[0]
    page = context.new_page()
    # Douyin shows the scan-or-phone login dialog on the home page; open it and
    # let the QR render. (Calibrate against the live Douyin login DOM if it changes.)
    page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
    return browser, context, page


def _capture_qr(page: Any, png_path: str) -> bool:
    """Screenshot the Douyin login QR to ``png_path``; True on success."""
    # Prefer the scan-code tab if the dialog defaults to phone login.
    try:
        tab = page.get_by_text("扫码登录", exact=False).first
        if tab.count() > 0:
            tab.click()
            page.wait_for_timeout(400)
    except Exception:
        pass
    candidates = [
        "canvas",
        'img[src*="qrcode"]',
        'img[src*="qr"]',
        '[class*="qrcode"] img',
        '[class*="qr-code"] img',
        '[class*="scan"] canvas',
        'img[src*="login"]',
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                el.screenshot(path=png_path)
                return True
        except Exception:
            continue
    # Fallback: the container around the 扫码登录 prompt.
    try:
        anchor = page.get_by_text("扫码登录", exact=False).first
        if anchor.count() > 0:
            container = anchor.locator("..")
            if container.count() > 0:
                container.screenshot(path=png_path)
                return True
    except Exception:
        pass
    return False


def _login_cookies_present(context: Any) -> bool:
    try:
        cookies = context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return any(k in names for k in LOGIN_COOKIE_KEYS)


def _is_logged_in(page: Any, context: Any) -> bool:
    if _login_cookies_present(context):
        return True
    try:
        url = page.url
    except Exception:
        return False
    if "creator-micro" in url:
        return True
    if "douyin.com" in url and "passport" not in url and "login" not in url:
        return True
    return False


def _is_qr_scanned(page: Any) -> bool:
    for hint in ("扫描成功", "已扫描", "请在手机上确认", "已扫码"):
        try:
            if page.get_by_text(hint, exact=False).count() > 0:
                return True
        except Exception:
            pass
    return False


class QrSession:
    """Thread-safe live login session state (never holds the page object)."""

    def __init__(self, session_id: str, alias: str, cookie_file: str, png_path: str):
        self.session_id = session_id
        self.alias = alias
        self.cookie_file = cookie_file
        self.png_path = png_path
        self.created_at = time.time()
        self.status = "starting"  # starting -> waiting -> scanning -> bound -> error/expired
        self.error: str | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def set_status(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self.status = status
            if error is not None:
                self.error = error

    def get_status(self):
        with self._lock:
            return self.status, self.error

    def request_stop(self) -> None:
        self._stop.set()

    def should_stop(self) -> bool:
        return self._stop.is_set()


class QrLoginManager:
    """In-memory registry of headless QR login sessions (single-thread driven)."""

    def __init__(
        self,
        sessions: dict[str, QrSession] | None = None,
        launcher: Callable[[str], Any] | None = None,
        run_in_thread: bool = True,
    ):
        self._sessions: dict[str, QrSession] = sessions if sessions is not None else {}
        self._lock = threading.Lock()
        self._launcher: Callable[[str], Any] = launcher or _launch_qr_browser
        self._run_in_thread = run_in_thread

    def start(self, alias: str, cookie_file: str) -> dict[str, Any]:
        """Begin a headless QR session; return {session_id, qr_image_url, error}."""
        _ensure_dirs()
        session_id = uuid.uuid4().hex
        png_path = os.path.join(QR_IMAGES_ROOT, f"qr_{session_id}.png")
        session = QrSession(session_id, alias, cookie_file, png_path)
        with self._lock:
            self._sessions[session_id] = session
        if self._run_in_thread:
            threading.Thread(target=self._run, args=(session,), daemon=True).start()
        else:
            self._run(session)  # tests drive synchronously
        return {
            "session_id": session_id,
            "qr_image_url": f"/api/accounts/qr-image?session={session_id}",
            "error": None,
        }

    def _run(self, session: QrSession) -> None:
        try:
            browser, context, page = self._launcher(session.alias)
        except Exception as e:  # e.g. camoufox/firefox not installed
            session.set_status("error", f"无法启动 Camoufox: {e}")
            return
        try:
            # Capture the QR (retry briefly while it renders).
            qr_ok = False
            for _ in range(20):
                if session.should_stop():
                    return
                if _capture_qr(page, session.png_path):
                    qr_ok = True
                    break
                time.sleep(0.5)
            if not qr_ok:
                session.set_status(
                    "error",
                    "未能捕获二维码（抖音可能启用了反爬校验，请改用 CLI: dy account add）",
                )
                return
            session.set_status("waiting")

            while time.time() - session.created_at < SESSION_TTL_SECONDS:
                if session.should_stop():
                    return
                if _is_logged_in(page, context):
                    for url in [
                        "https://www.douyin.com/",
                        "https://creator.douyin.com/creator-micro/content/manage",
                    ]:
                        try:
                            page.goto(url, wait_until="domcontentloaded")
                            page.wait_for_timeout(1200)
                        except Exception:
                            pass
                    os.makedirs(os.path.dirname(session.cookie_file), exist_ok=True)
                    context.storage_state(path=session.cookie_file)
                    from ..account_bridge import register_or_update_account

                    register_or_update_account(
                        session.alias,
                        cookie_file=session.cookie_file,
                        login_status="ready",
                    )
                    session.set_status("bound")
                    return
                session.set_status("scanning" if _is_qr_scanned(page) else "waiting")
                time.sleep(2)
            session.set_status("expired")
        except Exception as e:
            session.set_status("error", str(e))
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                if os.path.isfile(session.png_path):
                    os.remove(session.png_path)
            except OSError:
                pass
            st, _ = session.get_status()
            # Keep error/aborted sessions so the frontend can read the error;
            # bound/expired sessions are done and can be dropped immediately.
            if st in ("bound", "expired"):
                with self._lock:
                    self._sessions.pop(session.session_id, None)

    def status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return {"status": "gone"}
        st, err = session.get_status()
        return {"status": st, "error": err}

    def qr_image(self, session_id: str) -> str | None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        return session.png_path if os.path.isfile(session.png_path) else None

    def cancel(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            session.request_stop()

    def close(self, session_id: str) -> None:
        self.cancel(session_id)

    def sweep(self) -> int:
        """Drop expired/abandoned sessions; returns number removed."""
        removed = 0
        with self._lock:
            expired = [
                sid
                for sid, s in self._sessions.items()
                if time.time() - s.created_at > SESSION_TTL_SECONDS
            ]
            for sid in expired:
                s = self._sessions.pop(sid)
                s.request_stop()
                removed += 1
        return removed
