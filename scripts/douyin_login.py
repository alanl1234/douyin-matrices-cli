"""
抖音登录脚本 — Playwright 扫码登录，保存 Cookie。

Usage:
    python scripts/douyin_login.py
    python scripts/douyin_login.py --account work
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


async def login(account: str = "default"):
    """打开浏览器扫码登录抖音。"""
    from playwright.async_api import async_playwright

    # Cookie save path
    cookie_dir = os.path.expanduser("~/.dy/cookies")
    os.makedirs(cookie_dir, exist_ok=True)
    cookie_file = os.path.join(cookie_dir, f"{account}.json")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("[dy] 正在打开抖音创作者中心...")
        await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded")

        print("[dy] 请使用抖音 App 扫码登录")
        print("[dy] 扫码后浏览器会自动关闭")

        # Wait for login — detect navigation to creator dashboard
        try:
            await page.wait_for_url("**/creator-micro/**", timeout=120000)
            await page.wait_for_timeout(3000)
            print("[dy] 登录成功!")
        except Exception:
            print("[dy] 登录超时")
            await browser.close()
            return False

        # Save cookies (playwright storage_state format)
        await context.storage_state(path=cookie_file)
        print(f"[dy] Cookie 已保存: {cookie_file}")

        await browser.close()
        return True


def main():
    parser = argparse.ArgumentParser(description="抖音扫码登录")
    parser.add_argument("--account", default="default", help="账号名 (默认: default)")
    args = parser.parse_args()

    ok = asyncio.run(login(args.account))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
