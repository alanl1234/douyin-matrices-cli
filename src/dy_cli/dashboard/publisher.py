"""Execution layer for the orchestrator (对齐 xiaohongshu-matrices-cli publisher)。

Thin wrappers around the existing ``PlaywrightClient`` so the orchestrator only
deals with *scheduling & governance* and never with browser automation details.
All operations target a specific matrix account alias.
"""
from __future__ import annotations

import os

from dy_cli.engines.playwright_client import PlaywrightClient, PlaywrightError


def publish_for_account(
    account_alias: str,
    *,
    title: str,
    body: str,
    media_type: str = "video",
    media_paths: list[str] | None = None,
    tags: list[str] | None = None,
    visibility: str = "公开",
    schedule: str | None = None,
    headless: bool = True,
) -> dict:
    """Publish to a single matrix account. Returns the client result dict."""
    client = PlaywrightClient(account=account_alias, headless=headless)
    if not client.cookie_exists():
        raise PlaywrightError(f"账号 '{account_alias}' 未登录（请先 dy account add）")
    media_paths = media_paths or []
    if media_type == "image":
        abs_images = [os.path.abspath(p) if not p.startswith("http") else p for p in media_paths]
        return client.publish_image_text(
            title=title, content=body, images=abs_images, tags=tags, visibility=visibility, schedule_at=schedule
        )
    if media_paths:
        return client.publish_video(
            title=title,
            content=body,
            video_path=os.path.abspath(media_paths[0]),
            tags=tags,
            visibility=visibility,
            schedule_at=schedule,
        )
    raise PlaywrightError("发布缺少媒体文件（video 或 images）")


def interact_for_account(
    account_alias: str,
    aweme_id: str,
    action: str,
    *,
    content: str | None = None,
    headless: bool = True,
) -> dict:
    """Run an interaction (like/comment/favorite/follow) on a single account."""
    client = PlaywrightClient(account=account_alias, headless=headless)
    if not client.cookie_exists():
        raise PlaywrightError(f"账号 '{account_alias}' 未登录（请先 dy account add）")
    if action == "follow":
        return client.interact("", action, sec_user_id=aweme_id)
    return client.interact(aweme_id, action, content=content)
