"""
Chrome/Chromium 生命周期管理。

Usage:
    python scripts/chrome_launcher.py               # 启动 (Playwright Chromium)
    python scripts/chrome_launcher.py --headless     # 无头模式
    python scripts/chrome_launcher.py --kill         # 关闭
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys


async def launch_chromium(headless: bool = False):
    """使用 Playwright 启动 Chromium。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.douyin.com/", wait_until="domcontentloaded")

        endpoint = browser._impl_obj._browser.ws_endpoint if hasattr(browser._impl_obj, '_browser') else "N/A"
        print(f"[dy] Chromium 已启动")
        print(f"[dy] Headless: {headless}")
        print(f"[dy] 按 Ctrl+C 关闭")

        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await browser.close()
            print("[dy] Chromium 已关闭")


def kill_chromium():
    """关闭所有 Playwright Chromium 进程。"""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pkill", "-f", "chromium"], capture_output=True)
        elif sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "chromium.exe"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "chromium"], capture_output=True)
        print("[dy] Chromium 进程已终止")
    except Exception as e:
        print(f"[dy] 关闭失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="Chrome/Chromium 管理")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--kill", action="store_true", help="关闭 Chromium")
    args = parser.parse_args()

    if args.kill:
        kill_chromium()
    else:
        asyncio.run(launch_chromium(headless=args.headless))


if __name__ == "__main__":
    main()
