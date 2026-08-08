"""
抖音数据看板脚本 — Playwright 爬取 creator.douyin.com 数据。

Usage:
    python scripts/douyin_analytics.py
    python scripts/douyin_analytics.py --csv output.csv
    python scripts/douyin_analytics.py --account work
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys


async def get_analytics(account: str = "default", csv_file: str | None = None):
    """获取创作者数据看板。"""
    from playwright.async_api import async_playwright

    cookie_file = os.path.expanduser(f"~/.dy/cookies/{account}.json")
    if not os.path.isfile(cookie_file):
        print(f"[dy] Cookie 文件不存在: {cookie_file}")
        return None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=cookie_file)
        page = await context.new_page()

        try:
            print("[dy] 正在访问数据看板...")
            await page.goto(
                "https://creator.douyin.com/creator-micro/data/stats/self-content",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(5000)

            if await page.get_by_text("扫码登录").count() > 0:
                print("[dy] Cookie 已失效")
                return None

            # Extract data
            data = await page.evaluate("""() => {
                const rows = [];
                const items = document.querySelectorAll('tr, [class*="content-item"]');
                items.forEach(item => {
                    const cells = item.querySelectorAll('td, [class*="cell"]');
                    if (cells.length >= 3) {
                        const texts = Array.from(cells).map(c => c.textContent.trim());
                        rows.push({
                            '标题': texts[0] || '-',
                            '发布时间': texts[1] || '-',
                            '播放': texts[2] || '-',
                            '完播率': texts[3] || '-',
                            '点赞': texts[4] || '-',
                            '评论': texts[5] || '-',
                            '分享': texts[6] || '-',
                            '涨粉': texts[7] || '-',
                        });
                    }
                });
                return rows;
            }""")

            print(f"[dy] 获取到 {len(data)} 条数据")

            # Output JSON
            print(json.dumps(data, ensure_ascii=False, indent=2))

            # Export CSV
            if csv_file and data:
                keys = data[0].keys()
                with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    writer.writerows(data)
                print(f"[dy] CSV 已导出: {csv_file}")

            return data

        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description="抖音数据看板")
    parser.add_argument("--account", default="default", help="账号名")
    parser.add_argument("--csv", default=None, help="导出 CSV 文件路径")
    args = parser.parse_args()

    result = asyncio.run(get_analytics(args.account, args.csv))
    sys.exit(0 if result is not None else 1)


if __name__ == "__main__":
    main()
