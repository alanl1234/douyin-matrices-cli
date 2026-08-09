"""
dy publish — 发布命令 (Playwright 引擎)。
"""
from __future__ import annotations

import os

import click

from dy_cli.account_bridge import effective_account
from dy_cli.engines.playwright_client import PlaywrightClient, PlaywrightError
from dy_cli.utils import config
from dy_cli.utils.output import console, error, info, success


@click.command("publish", help="发布视频或图文到抖音")
@click.option("--title", "-t", required=True, help="标题")
@click.option("--content", "-c", default=None, help="描述正文")
@click.option("--content-file", type=click.Path(exists=True), default=None, help="从文件读取描述")
@click.option("--video", "-v", default=None, help="视频文件路径")
@click.option("--images", "-i", multiple=True, help="图片路径 (可多个)")
@click.option("--tags", multiple=True, help="标签 (可多个，如: --tags 旅行 --tags 美食)")
@click.option("--mention", "-m", multiple=True, help="@好友 (可多个，如: --mention 抖音小助手)")
@click.option("--visibility", type=click.Choice(["公开", "好友可见", "仅自己可见"]),
              default="公开", help="可见范围")
@click.option("--schedule", default=None, help="定时发布 (ISO 8601, 如 2026-03-16T10:00:00+08:00)")
@click.option("--thumbnail", default=None, type=click.Path(exists=True), help="封面图片路径")
@click.option("--account", default=None, help="使用指定账号（局部覆盖全局 --account）")
@click.option("--headless", is_flag=True, help="无头模式 (不显示浏览器)")
@click.option("--dry-run", is_flag=True, help="预览模式，不实际发布")
@click.pass_context
def publish(ctx, title, content, content_file, video, images, tags, mention, visibility, schedule, thumbnail, account, headless, dry_run):
    """发布视频或图文。"""
    account = account or effective_account(ctx)

    # Handle content
    if content_file:
        with open(content_file, encoding="utf-8") as f:
            content = f.read().strip()
    if not content:
        content = ""

    # Validate media
    images = list(images)
    if not images and not video:
        error("必须提供视频 (--video) 或图片 (--images)")
        raise SystemExit(1)

    if video and images:
        error("不能同时提供视频和图片，请选择一种")
        raise SystemExit(1)

    # Validate files
    if video and not video.startswith("http") and not os.path.isfile(video):
        error(f"视频文件不存在: {video}")
        raise SystemExit(1)

    for img in images:
        if not img.startswith("http") and not os.path.isfile(img):
            error(f"图片文件不存在: {img}")
            raise SystemExit(1)

    tags = list(tags)
    mentions = list(mention)

    # Dry run
    if dry_run:
        console.print()
        info("📋 发布预览:")
        console.print(f"  [bold]标题:[/] {title}")
        console.print(f"  [bold]描述:[/] {content[:100]}{'...' if len(content) > 100 else ''}")
        if video:
            console.print(f"  [bold]视频:[/] {video}")
        else:
            console.print(f"  [bold]图片:[/] {', '.join(images)}")
        if tags:
            console.print(f"  [bold]标签:[/] {', '.join(tags)}")
        if mentions:
            console.print(f"  [bold]@好友:[/] {', '.join(mentions)}")
        console.print(f"  [bold]可见:[/] {visibility}")
        if schedule:
            console.print(f"  [bold]定时:[/] {schedule}")
        if thumbnail:
            console.print(f"  [bold]封面:[/] {thumbnail}")
        console.print()
        return

    # Publish
    cfg = config.load_config()
    use_headless = headless or cfg["playwright"].get("headless", False)

    client = PlaywrightClient(
        account=account,
        headless=use_headless,
        slow_mo=cfg["playwright"].get("slow_mo", 0),
    )

    if not client.cookie_exists():
        error("未登录，请先运行: dy login")
        raise SystemExit(1)

    try:
        if video:
            info(f"正在发布视频: {os.path.basename(video)}")
            result = client.publish_video(
                title=title,
                content=content,
                video_path=os.path.abspath(video),
                tags=tags or None,
                visibility=visibility,
                schedule_at=schedule,
                thumbnail_path=thumbnail,
                mentions=mentions or None,
            )
        else:
            info(f"正在发布图文 ({len(images)} 张图片)")
            result = client.publish_image_text(
                title=title,
                content=content,
                images=[os.path.abspath(img) if not img.startswith("http") else img for img in images],
                tags=tags or None,
                visibility=visibility,
                schedule_at=schedule,
                mentions=mentions or None,
            )

        status = result.get("status", "submitted")
        if status == "published":
            success("发布成功! 🎉")
            if result.get("url"):
                info(f"作品链接: {result['url']}")
        elif status == "failed":
            error(f"发布失败: {result.get('message', '未知原因')}")
            raise SystemExit(1)
        else:
            info("发布请求已提交，但未检测到明确结果，请到创作者中心确认")

    except PlaywrightError as e:
        error(f"发布失败: {e}")
        raise SystemExit(1)
    except Exception as e:  # 兜底：未预期异常也转为友好报错，避免 traceback 中断整批
        error(f"发布异常: {type(e).__name__}: {e}")
        raise SystemExit(1)
