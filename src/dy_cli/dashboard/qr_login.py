"""Headless QR-code login for the dashboard, powered by Playwright (Chromium).

Why Playwright (not Camoufox): unify on a single browser engine. Publishing and
scraping already use Playwright Chromium, so using it for QR login too means ONE
engine to install (no separate Firefox) and native ``storage_state`` cookies that
the PlaywrightClient can load directly — this fixes the previous cross-engine
(Firefox -> Chromium) cookie incompatibility that broke CLI publishing.

Anti-detection: bare headless Chromium exposes ``navigator.webdriver`` and the
``AutomationControlled`` flag, which Douyin's passport may use to refuse the QR or
drop the session. We apply an undetected-chromium style patch (``add_init_script``
+ launch args) so the QR renders and the session sticks.

QR expiry: Douyin login QR TTL is ~2 minutes. The poll loop keeps the served PNG
fresh — it refreshes proactively well before expiry (unless the user is mid-scan)
and also reacts to the explicit "expired" overlay — so the web page always shows a
scannable code and binding never silently times out.

Architecture (single-thread safe, unchanged):
- ``start()`` launches Playwright inside ONE dedicated background thread that
  drives the whole login poll loop; the page handle never crosses threads.
- Main-thread ``status()`` / ``qr_image()`` / ``cancel()`` / ``close()`` only read
  a thread-safe state object or the QR PNG file.
- On success, ``context.storage_state()`` exports a Playwright storage_state JSON
  straight to ``~/.dy/cookies/<alias>.json``.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable

# Douyin QR codes live ~2 minutes; keep a comfortable margin before reporting
# the whole session expired. The inner QR is refreshed more often (see below).
SESSION_TTL_SECONDS = 300
# Refresh the QR well under Douyin's ~2min TTL so it never actually expires
# before the user scans (skipped while the user is mid-scan -> "scanning").
QR_REFRESH_INTERVAL = 90

PROFILES_ROOT = os.path.join(os.path.expanduser("~"), ".dy", "playwright_profiles")
QR_IMAGES_ROOT = os.path.join(os.path.expanduser("~"), ".dy", "qr_images")

# Any of these cookie names appearing means a logged-in session has landed.
LOGIN_COOKIE_KEYS = ("sessionid_ss", "sid_tt", "sid_guard", "sessionid")

# Hints that the QR has expired / needs a refresh click.
QR_EXPIRED_HINTS = ("二维码已失效", "已失效", "点击刷新二维码", "二维码已过期", "重新扫码")
# Buttons/links that trigger a QR refresh.
QR_REFRESH_HINTS = ("刷新", "点击刷新", "二维码已失效", "重新扫码", "重新获取")


def _ensure_dirs() -> None:
    os.makedirs(PROFILES_ROOT, exist_ok=True)
    os.makedirs(QR_IMAGES_ROOT, exist_ok=True)


def _launch_qr_browser(alias: str):
    """Launch Playwright Chromium (stealth) for QR login; return (browser, context, page).

    Imports Playwright lazily so this module imports cleanly without the browser
    runtime (keeps CI import checks green). The Playwright driver handle is stashed
    on the browser object (``_pw``) for cleanup in the caller's ``finally``.
    """
    from playwright.sync_api import sync_playwright

    profile_dir = os.path.join(PROFILES_ROOT, alias)
    os.makedirs(profile_dir, exist_ok=True)
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
        ],
    )
    context = browser.new_context(
        locale="zh-CN",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    # Anti-detection: hide the webdriver flag and re-add a believable chrome runtime.
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        "try { window.chrome = {runtime: {}}; } catch (e) {}"
    )
    page = context.new_page()
    # Douyin shows the scan-or-phone login dialog on the home page; open it and
    # let the QR render. (Calibrate against the live Douyin login DOM if it changes.)
    page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
    browser._pw = pw  # keep driver handle for cleanup
    return browser, context, page


def _capture_qr(page: Any, png_path: str) -> bool:
    """Screenshot the Douyin login QR to ``png_path``; True on success.

    Writes to a ``.tmp`` file first, then atomically ``os.replace``-es it onto
    the final path so a concurrent ``/qr-image`` GET never reads a half-written
    PNG (Playwright's ``screenshot(path=...)`` is not atomic on its own).
    """
    candidates = [
        "canvas",
        'img[src*="qrcode"]',
        'img[src*="qr"]',
        '[class*="qrcode"] img',
        '[class*="qr-code"] img',
        '[class*="scan"] canvas',
        'img[src*="login"]',
    ]
    tmp_path = png_path + ".tmp"
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                el.screenshot(path=tmp_path)
                os.replace(tmp_path, png_path)
                return True
        except Exception:
            continue
    # Fallback: the container around the 扫码登录 prompt.
    try:
        anchor = page.get_by_text("扫码登录", exact=False).first
        if anchor.count() > 0:
            container = anchor.locator("..")
            if container.count() > 0:
                container.screenshot(path=tmp_path)
                os.replace(tmp_path, png_path)
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
    """判定是否已登录。

    登录态**只能**以登录 cookie 为依据，绝不能依据页面 URL。抖音首页在
    未登录时 URL 仍然是 ``https://www.douyin.com/``，若用
    ``"douyin.com" in url`` 这类启发式判断，会误判为已登录，导致用户尚未
    扫码就直接 ``bound``（表现为「无操作显示绑定成功」，且二维码图片因
    会话瞬间结束被删除而加载失败）。登录成功后抖音会写入 ``sessionid_ss`` /
    ``sid_tt`` 等 cookie，这才是权威证据。

    ``page`` 参数保留以兼容调用点与测试 mock，当前判定不使用它。
    """
    return _login_cookies_present(context)


def _is_qr_scanned(page: Any) -> bool:
    for hint in ("扫描成功", "已扫描", "请在手机上确认", "已扫码"):
        try:
            if page.get_by_text(hint, exact=False).count() > 0:
                return True
        except Exception:
            pass
    return False


def _is_qr_expired(page: Any) -> bool:
    """Detect Douyin's 'QR expired' overlay."""
    for hint in QR_EXPIRED_HINTS:
        try:
            if page.get_by_text(hint, exact=False).count() > 0:
                return True
        except Exception:
            pass
    return False


def _refresh_qr(page: Any) -> None:
    """Refresh the Douyin login QR (its TTL expired)."""
    for hint in QR_REFRESH_HINTS:
        try:
            btn = page.get_by_text(hint, exact=False).first
            if btn.count() > 0:
                btn.click()
                page.wait_for_timeout(600)
                return
        except Exception:
            continue
    # Fallback: reload the login page to regenerate the QR.
    try:
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
    except Exception:
        pass


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
        self.refreshed_at = time.time()
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
        except Exception as e:  # e.g. playwright/chromium not installed
            session.set_status("error", f"无法启动浏览器(Playwright): {e}")
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

                # User is mid-scan (phone shows confirmation) -> don't disrupt.
                if _is_qr_scanned(page):
                    session.set_status("scanning")
                    time.sleep(2)
                    continue

                # Keep the QR fresh: react to the explicit expiry overlay, and
                # proactively refresh before Douyin's ~2min TTL elapses.
                if _is_qr_expired(page) or (
                    time.time() - session.refreshed_at > QR_REFRESH_INTERVAL
                ):
                    _refresh_qr(page)
                    for _ in range(10):
                        if session.should_stop():
                            return
                        if _capture_qr(page, session.png_path):
                            break
                        time.sleep(0.3)
                    session.refreshed_at = time.time()
                    session.set_status("waiting")
                    continue

                session.set_status("waiting")
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
                pw = getattr(browser, "_pw", None)
                if pw is not None:
                    pw.stop()
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
