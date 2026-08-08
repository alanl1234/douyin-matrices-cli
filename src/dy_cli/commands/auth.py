"""
dy login / logout / status — 认证命令。

支持两种登录方式:
1. 浏览器 Cookie 自动提取 (默认, 零摩擦)
2. Playwright 扫码 (--qrcode)
"""
from __future__ import annotations

import json
import os

import click

from dy_cli.account_bridge import list_matrix_accounts, register_or_update_account
from dy_cli.engines.playwright_client import PlaywrightClient, PlaywrightError
from dy_cli.utils import config
from dy_cli.utils.output import console, error, info, status, success, warning


def _register_login(account: str | None) -> None:
    """Best-effort: register a successful login into the matrix DB."""
    try:
        name = account or config.load_config()["default"]["account"]
        register_or_update_account(
            name, cookie_file=config.get_cookie_file(name), login_status="ready"
        )
    except Exception:
        pass  # 后台登记失败不影响登录本身


def _print_matrix_accounts() -> None:
    """Show the matrix roster (for `dy status`)."""
    accounts = list_matrix_accounts()
    if not accounts:
        return
    console.print("")
    console.print("[bold]账号矩阵（可用 --account 指定目标）：[/bold]")
    for acc in accounts:
        st = acc.get("login_status") or "?"
        marker = "✓" if st in ("ready", "legacy") else "·"
        label = acc.get("nickname") or acc.get("alias") or "?"
        ident = f"id={acc.get('id')}" if acc.get("id") else f"alias={acc.get('alias')}"
        extra = f" / douyin_user_id={acc['douyin_user_id']}" if acc.get("douyin_user_id") else ""
        console.print(
            f"  [{marker}] {label}  ({ident}{extra})  [{st}]"
            + ("  ⚠️ 无 cookie 文件" if not acc.get("has_cookie_file") else "")
        )


def _extract_browser_cookies(account: str | None = None) -> bool:
    """从浏览器自动提取抖音 Cookie（需要用户在浏览器中已登录抖音）。"""
    try:
        import browser_cookie3 as bc3
    except ImportError:
        return False

    # 从多个域名收集 cookie
    all_cookies: dict[str, dict] = {}
    browsers = ["chrome", "firefox", "edge", "brave", "opera", "chromium", "safari"]
    domains = [".douyin.com", "www.douyin.com", "creator.douyin.com"]

    found_browser = None
    for browser_name in browsers:
        loader = getattr(bc3, browser_name, None)
        if not loader:
            continue
        for domain in domains:
            try:
                jar = loader(domain_name=domain)
                for c in jar:
                    if "douyin" in (c.domain or ""):
                        all_cookies[c.name] = {
                            "name": c.name,
                            "value": c.value,
                            "domain": c.domain,
                            "path": c.path or "/",
                        }
                        found_browser = browser_name
            except Exception:
                continue
        if all_cookies:
            break

    if not all_cookies:
        return False

    # 检查是否有关键 session cookie
    key_names = {"sessionid", "passport_csrf_token", "odin_tt", "sid_guard"}
    has_session = bool(key_names & set(all_cookies.keys()))

    if not has_session:
        info(f"从 {found_browser} 提取了 {len(all_cookies)} 个 cookie，但缺少登录态")
        info("请先在浏览器中登录 douyin.com，然后重试")
        return False

    # 保存为 Playwright storage_state 格式
    cookie_file = config.get_cookie_file(account)
    os.makedirs(os.path.dirname(cookie_file), exist_ok=True)
    storage = {
        "cookies": list(all_cookies.values()),
        "origins": [],
    }
    with open(cookie_file, "w", encoding="utf-8") as f:
        json.dump(storage, f, ensure_ascii=False, indent=2)
    info(f"从 {found_browser} 提取了 {len(all_cookies)} 个 cookie (含登录态)")
    return True


@click.command("login", help="登录抖音")
@click.option("--account", default=None, help="账号名")
@click.option("--browser", is_flag=True, help="从浏览器提取 Cookie (需在浏览器中已登录抖音)")
def login(account, browser):
    """登录抖音。默认扫码登录，--browser 从浏览器提取 Cookie。"""
    cfg = config.load_config()

    # 已登录检查
    client = PlaywrightClient(account=account, headless=True)
    if client.cookie_exists():
        try:
            if client.check_login():
                success("已登录抖音")
                if not click.confirm("是否重新登录?", default=False):
                    return
        except Exception:
            pass

    # 方式 1: 从浏览器提取 Cookie
    if browser:
        info("正在从浏览器提取 Cookie...")
        if _extract_browser_cookies(account):
            _register_login(account)
            success("登录成功! 🎉 (从浏览器提取)")
            return
        else:
            warning("浏览器 Cookie 提取失败，切换到扫码模式")

    # 方式 2: Playwright 扫码 (默认)
    info("正在打开浏览器，请使用抖音 App 扫码...")
    pw_client = PlaywrightClient(
        account=account,
        headless=False,
        slow_mo=cfg["playwright"].get("slow_mo", 0),
    )
    try:
        ok = pw_client.login()
        if ok:
            _register_login(account)
            success("登录成功! 🎉")
        else:
            error("登录超时或失败")
            raise SystemExit(1)
    except PlaywrightError as e:
        error(f"登录失败: {e}")
        raise SystemExit(1)


@click.command("logout", help="退出登录")
@click.option("--account", default=None, help="账号名")
def logout(account):
    """退出登录（删除 Cookie）。"""
    client = PlaywrightClient(account=account)
    if client.logout():
        success("已退出登录，Cookie 已删除")
    else:
        info("未找到登录凭据")


@click.command("status", help="查看登录状态")
@click.option("--account", default=None, help="账号名")
def auth_status(account):
    """检查登录状态。"""
    console.print()
    client = PlaywrightClient(account=account)

    if not client.cookie_exists():
        status("登录状态", "未登录 (无 Cookie 文件)", "red")
        info("使用 [bold]dy login[/] 登录")
    else:
        info("正在验证 Cookie...")
        try:
            logged_in = client.check_login()
            if logged_in:
                status("登录状态", "已登录", "green")
                status("Cookie", client.cookie_file, "dim")
            else:
                status("登录状态", "Cookie 已失效", "yellow")
                info("使用 [bold]dy login[/] 重新登录")
        except Exception as e:
            status("登录状态", f"检查失败: {e}", "red")

    _print_matrix_accounts()
    console.print()
