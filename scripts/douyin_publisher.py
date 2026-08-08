"""
抖音发布脚本 — Playwright 操控 creator.douyin.com 上传视频/图文。

Usage:
    python scripts/douyin_publisher.py --title "标题" --content "描述" --video video.mp4
    python scripts/douyin_publisher.py --title "标题" --content "描述" --images img1.jpg img2.jpg
    python scripts/douyin_publisher.py --title "标题" --content "描述" --video video.mp4 --account work
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


async def publish_video(
    title: str,
    content: str,
    video_path: str,
    tags: list[str] | None = None,
    account: str = "default",
    headless: bool = False,
):
    """发布视频到抖音创作者中心。"""
    from playwright.async_api import async_playwright

    cookie_file = os.path.expanduser(f"~/.dy/cookies/{account}.json")
    if not os.path.isfile(cookie_file):
        print(f"[dy] Cookie 文件不存在: {cookie_file}")
        print("[dy] 请先运行: python scripts/douyin_login.py")
        return False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(storage_state=cookie_file)
        page = await context.new_page()

        try:
            print("[dy] 正在打开上传页面...")
            await page.goto(
                "https://creator.douyin.com/creator-micro/content/upload",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(3000)

            # Check login
            if await page.get_by_text("扫码登录").count() > 0:
                print("[dy] Cookie 已失效，请重新登录")
                return False

            # Upload video
            print(f"[dy] 上传视频: {os.path.basename(video_path)}")
            upload_input = page.locator('input[type="file"]').first
            await upload_input.set_input_files(os.path.abspath(video_path))

            # Wait for upload
            print("[dy] 等待上传完成...")
            for _ in range(120):
                ready = await page.locator('[contenteditable="true"]').count()
                if ready > 0:
                    break
                await page.wait_for_timeout(5000)

            # Fill content
            editor = page.locator('[contenteditable="true"]').first
            await editor.click()

            full_text = content
            if tags:
                full_text += " " + " ".join(f"#{t}" for t in tags)

            await page.keyboard.type(full_text, delay=50)
            print("[dy] 内容已填写")

            # Click publish
            await page.wait_for_timeout(2000)
            publish_btn = page.locator('button:has-text("发布")').first
            try:
                await publish_btn.click()
                await page.wait_for_timeout(5000)
                print("[dy] ✅ 发布成功!")
                return True
            except Exception:
                print("[dy] 未找到发布按钮，请手动确认")
                if not headless:
                    await page.wait_for_timeout(30000)
                return False

        finally:
            await context.storage_state(path=cookie_file)
            await browser.close()


async def publish_images(
    title: str,
    content: str,
    images: list[str],
    tags: list[str] | None = None,
    account: str = "default",
    headless: bool = False,
):
    """发布图文到抖音创作者中心。"""
    from playwright.async_api import async_playwright

    cookie_file = os.path.expanduser(f"~/.dy/cookies/{account}.json")
    if not os.path.isfile(cookie_file):
        print(f"[dy] Cookie 文件不存在: {cookie_file}")
        return False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(storage_state=cookie_file)
        page = await context.new_page()

        try:
            await page.goto(
                "https://creator.douyin.com/creator-micro/content/upload",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(3000)

            if await page.get_by_text("扫码登录").count() > 0:
                print("[dy] Cookie 已失效")
                return False

            # Switch to image tab
            try:
                img_tab = page.locator('text=图文').first
                if await img_tab.count() > 0:
                    await img_tab.click()
                    await page.wait_for_timeout(1000)
            except Exception:
                pass

            # Upload images
            abs_images = [os.path.abspath(img) for img in images]
            upload_input = page.locator('input[type="file"]').first
            await upload_input.set_input_files(abs_images)
            print(f"[dy] 上传 {len(abs_images)} 张图片")
            await page.wait_for_timeout(3000)

            # Fill content
            editor = page.locator('[contenteditable="true"]').first
            await editor.click()

            full_text = content
            if tags:
                full_text += " " + " ".join(f"#{t}" for t in tags)

            await page.keyboard.type(full_text, delay=50)

            # Publish
            await page.wait_for_timeout(2000)
            publish_btn = page.locator('button:has-text("发布")').first
            try:
                await publish_btn.click()
                await page.wait_for_timeout(5000)
                print("[dy] ✅ 发布成功!")
                return True
            except Exception:
                print("[dy] 请手动确认发布")
                return False

        finally:
            await context.storage_state(path=cookie_file)
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description="抖音内容发布")
    parser.add_argument("--title", "-t", required=True, help="标题")
    parser.add_argument("--content", "-c", required=True, help="描述")
    parser.add_argument("--video", "-v", default=None, help="视频文件路径")
    parser.add_argument("--images", "-i", nargs="+", default=None, help="图片文件路径")
    parser.add_argument("--tags", nargs="+", default=None, help="标签")
    parser.add_argument("--account", default="default", help="账号名")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    args = parser.parse_args()

    if args.video:
        ok = asyncio.run(publish_video(
            args.title, args.content, args.video,
            tags=args.tags, account=args.account, headless=args.headless,
        ))
    elif args.images:
        ok = asyncio.run(publish_images(
            args.title, args.content, args.images,
            tags=args.tags, account=args.account, headless=args.headless,
        ))
    else:
        print("[dy] 请指定 --video 或 --images")
        sys.exit(1)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
