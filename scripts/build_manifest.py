#!/usr/bin/env python
"""由「内容清单」生成 `dy publish-batch` 可用的发布清单。

内容清单（posts.json）示例::

    {
      "src_dir": "C:/imgs",            // 图片目录，缺省＝清单所在目录
      "tags": ["初中物理", "物理实验"],  // 缺省标签，可被单条 tags 覆盖
      "posts": [
        {
          "no": 1,
          "title": "标题（≤20 字）",
          "caption": "正文（单段，≤1000 字）",
          "images": ["01.png", "02.png"],   // 顺序即发布页序
          "schedule": null                   // 留空由脚本按 --start/--every 生成
        }
      ]
    }

生成结果直接喂给 CLI::

    python scripts/build_manifest.py posts.json --start 2026-12-01 --every 1 --hour 15
    dy publish-batch manifest.json --dry-run

只做本地计算与校验，不联网、不碰浏览器。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

# 允许未安装时直接从源码树运行
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dy_cli.utils.schedule_window import ScheduleWindowError, schedule_text, validate_schedule  # noqa: E402

TITLE_LIMIT = 20
CAPTION_LIMIT = 1000


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build(data: dict, start: date, every: int, hour: str, base_dir: str) -> dict:
    posts = data.get("posts") or []
    if not posts:
        raise SystemExit("内容清单里没有 posts")

    src_dir = data.get("src_dir") or base_dir
    if not os.path.isabs(src_dir):
        src_dir = os.path.normpath(os.path.join(base_dir, src_dir))
    default_tags = data.get("tags") or []

    used_images: dict[str, int] = {}
    seen_titles: dict[str, int] = {}
    seen_slots: dict[str, int] = {}
    out: list[dict] = []

    for idx, p in enumerate(posts, start=1):
        no = int(p.get("no") or idx)
        title = (p.get("title") or "").strip()
        caption = (p.get("caption") or p.get("content") or "").strip()
        images = p.get("images") or []

        if not title:
            raise SystemExit(f"#{no} 缺少 title")
        if len(title) > TITLE_LIMIT:
            raise SystemExit(f"#{no} 标题 {len(title)} 字，超过平台上限 {TITLE_LIMIT}")
        if len(caption) > CAPTION_LIMIT:
            raise SystemExit(f"#{no} 正文 {len(caption)} 字，超过平台上限 {CAPTION_LIMIT}")
        if not images:
            raise SystemExit(f"#{no} 缺少 images")
        if title in seen_titles:
            raise SystemExit(f"#{no} 标题与 #{seen_titles[title]} 重复：{title}")
        seen_titles[title] = no

        paths: list[str] = []
        for name in images:
            full = name if os.path.isabs(name) else os.path.normpath(os.path.join(src_dir, name))
            if not os.path.isfile(full):
                raise SystemExit(f"#{no} 图片不存在: {full}")
            if full in used_images:
                raise SystemExit(f"#{no} 复用图片 {name}（已被 #{used_images[full]} 使用）")
            used_images[full] = no
            paths.append(full)

        if p.get("schedule"):
            raw = p["schedule"]
        else:
            day = start + timedelta(days=every * (len(out)))
            raw = f"{day.isoformat()}T{hour}:00+08:00"
        try:
            slot = schedule_text(raw)
        except ScheduleWindowError as exc:
            raise SystemExit(f"#{no} 定时时间无法解析: {exc}") from exc
        if slot in seen_slots:
            raise SystemExit(f"#{no} 与 #{seen_slots[slot]} 定时时间重复: {slot}")
        seen_slots[slot] = no

        out.append({
            "no": no,
            "module": p.get("module"),
            "experiment": p.get("experiment"),
            "theme": p.get("theme"),
            "style": p.get("style"),
            "title": title,
            "caption": caption,
            "tags": p.get("tags") or default_tags,
            "images": paths,
            "schedule": raw,
        })

    return {
        "account": data.get("account") or "default",
        "start_date": start.isoformat(),
        "every_days": every,
        "time": hour,
        "total": len(out),
        "posts": out,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 dy publish-batch 清单")
    ap.add_argument("posts", help="内容清单 JSON")
    ap.add_argument("--out", default=None, help="输出文件（默认同目录 manifest.json）")
    ap.add_argument("--start", default=None, help="首条日期 YYYY-MM-DD（默认明天）")
    ap.add_argument("--every", type=int, default=1, help="间隔天数（1=每天一条）")
    ap.add_argument("--hour", default="15:00", help="定时时刻 HH:MM")
    ap.add_argument("--check-window", action="store_true", help="校验落在平台可约窗口内")
    args = ap.parse_args()

    posts_path = os.path.abspath(args.posts)
    base_dir = os.path.dirname(posts_path)
    data = _load(posts_path)

    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start = date.today() + timedelta(days=1)

    manifest = build(data, start, args.every, args.hour, base_dir)

    if args.check_window:
        for p in manifest["posts"]:
            try:
                validate_schedule(p["schedule"])
            except ScheduleWindowError as exc:
                raise SystemExit(f"#{p['no']} 不在可约窗口: {exc}") from exc

    out_path = args.out or os.path.join(base_dir, "manifest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    first = schedule_text(manifest["posts"][0]["schedule"])
    last = schedule_text(manifest["posts"][-1]["schedule"])
    print(f"OK {manifest['total']} 条 / {len(set(i for p in manifest['posts'] for i in p['images']))} 张图 / {first} → {last}")
    print(f"manifest: {out_path}")
    if not args.check_window:
        print("提示：加 --check-window 可校验是否落在 2 小时~14 天窗口内")


if __name__ == "__main__":
    main()
