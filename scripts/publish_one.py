#!/usr/bin/env python
"""从发布清单里挑一条单发（灰度用）。

与 `dy publish-batch` 共用同一份清单和同一套引擎校验，差别只有「只发一条」，
适合批量前先验证一条的排版/页序/话题/定时是否都符合预期。

    python scripts/publish_one.py manifest.json --no 1 --dry-run   # 只看计划
    python scripts/publish_one.py manifest.json --no 1             # 真发这一条

退出码：0=成功，1=校验或发布失败（不做重试、不自动换条目）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dy_cli.engines.playwright_client import PlaywrightClient, PlaywrightError  # noqa: E402
from dy_cli.utils import config  # noqa: E402
from dy_cli.utils.schedule_window import ScheduleWindowError, schedule_text, validate_schedule  # noqa: E402


def pick(manifest: dict, no: int) -> dict:
    for i, post in enumerate(manifest.get("posts") or [], start=1):
        if int(post.get("no") or i) == no:
            return post
    raise SystemExit(f"清单里没有 #{no}")


def main() -> None:
    ap = argparse.ArgumentParser(description="单条定时发布（灰度）")
    ap.add_argument("manifest", help="publish-batch 清单")
    ap.add_argument("--no", type=int, required=True, help="清单里的序号")
    ap.add_argument("--account", default=None, help="账号名（默认取清单 account）")
    ap.add_argument("--headless", action="store_true", help="无头模式")
    ap.add_argument("--dry-run", action="store_true", help="只校验并打印，不发布")
    args = ap.parse_args()

    path = os.path.abspath(args.manifest)
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    post = pick(manifest, args.no)
    account = args.account or manifest.get("account") or "default"

    # 图片相对清单所在目录
    base = os.path.dirname(path)
    images = [
        p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))
        for p in (post.get("images") or [])
    ]
    video = post.get("video")
    if video and not os.path.isabs(video):
        video = os.path.normpath(os.path.join(base, video))

    for p in images:
        if not os.path.isfile(p):
            raise SystemExit(f"图片不存在: {p}")
    if video and not os.path.isfile(video):
        raise SystemExit(f"视频不存在: {video}")

    try:
        slot = schedule_text(post["schedule"])
        validate_schedule(post["schedule"])
    except ScheduleWindowError as exc:
        raise SystemExit(f"定时时间不可用: {exc}") from exc

    tags = post.get("tags") or []
    print(f"#{args.no} {post['title']}")
    print(f"  定时 {slot}  图 {len(images) or 1}  话题 {len(tags)}  账号 {account}")
    if args.dry_run:
        print("dry-run：未发布")
        return

    cfg = config.load_config()
    client = PlaywrightClient(
        account=account,
        headless=args.headless or cfg["playwright"].get("headless", False),
        slow_mo=cfg["playwright"].get("slow_mo", 0),
    )

    try:
        if video:
            result = client.publish_video(
                title=post["title"],
                content=post.get("caption") or post.get("content") or "",
                video_path=video,
                tags=tags or None,
                visibility=post.get("visibility", "公开"),
                schedule_at=slot,
                thumbnail_path=post.get("thumbnail"),
                mentions=post.get("mentions") or None,
            )
        else:
            result = client.publish_image_text(
                title=post["title"],
                content=post.get("caption") or post.get("content") or "",
                images=images,
                tags=tags or None,
                visibility=post.get("visibility", "公开"),
                schedule_at=slot,
                mentions=post.get("mentions") or None,
            )
    except PlaywrightError as exc:
        raise SystemExit(f"发布失败: {exc}") from exc

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "published":
        raise SystemExit(f"状态异常: {result.get('status')} — {result.get('message')}")


if __name__ == "__main__":
    main()
