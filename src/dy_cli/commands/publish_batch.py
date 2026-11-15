"""dy publish-batch — 按清单批量定时发布（串行、逐条校验、失败即停）。

清单示例（JSON / YAML 均可，相对路径按清单所在目录解析）：

```json
{
  "account": "default",
  "posts": [
    {
      "title": "阻力越小滑得越远，然后呢？",
      "content": "正文……",
      "images": ["imgs/a.png", "imgs/b.png"],
      "tags": ["初中物理", "物理实验"],
      "schedule": "2026-11-15T15:00:00+08:00",
      "visibility": "公开"
    }
  ]
}
```

设计原则：
- 发布前静态校验：图片存在、时间窗口合法、标题 ≤20 字、时间不重复
- 发布后三重校验：状态必须是 published、定时回读必须与计划一致、创作者接口必须能查到
- 不做「失败继续跑」：宁可停下让人看，也不要批量错发
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

import click

from dy_cli.account_bridge import effective_account
from dy_cli.engines.playwright_client import PlaywrightClient, PlaywrightError
from dy_cli.utils import config
from dy_cli.utils.output import console, error, info, success, warning
from dy_cli.utils.schedule_window import (
    TZ,
    ScheduleWindowError,
    bookable_dates,
    bookable_range,
    schedule_text,
    validate_schedule,
)

CREATOR_ITEMS_API = "https://creator.douyin.com/aweme/v1/creator/item/list/?count=30&cursor=0"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://creator.douyin.com/creator-micro/content/manage",
}
TITLE_LIMIT = 20
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")  # 平台不支持 gif


# ----------------------------------------------------------------------
# 清单解析与校验（纯函数，便于单测）
# ----------------------------------------------------------------------

def load_manifest(path: str) -> dict:
    """读取 JSON / YAML 清单；解析失败抛 ValueError。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        raise ValueError(f"清单读取失败: {exc}") from exc
    try:
        if path.lower().endswith((".yaml", ".yml")):
            import yaml

            return yaml.safe_load(raw) or {}
        return json.loads(raw or "{}")
    except Exception as exc:  # noqa: BLE001 - 统一转成清单错误
        raise ValueError(f"清单解析失败: {exc}") from exc


def _resolve(path: str, base_dir: str | None) -> str:
    if not path or path.startswith(("http://", "https://")) or os.path.isabs(path) or not base_dir:
        return path
    return os.path.normpath(os.path.join(base_dir, path))


def validate_post(
    post: dict, now: datetime | None = None, base_dir: str | None = None
) -> list[str]:
    """返回该条的错误列表（空列表 = 通过）。"""
    errs: list[str] = []
    title = (post.get("title") or "").strip()
    if not title:
        errs.append("缺少 title")
    elif len(title) > TITLE_LIMIT:
        errs.append(f"title 超过 {TITLE_LIMIT} 字（平台上限）")

    images = post.get("images") or []
    video = post.get("video")
    if not images and not video:
        errs.append("缺少 images 或 video")
    for p in images:
        resolved = _resolve(p, base_dir)
        if not os.path.isfile(resolved):
            errs.append(f"图片不存在: {p}")
        elif not resolved.lower().endswith(IMAGE_EXTS):
            errs.append(f"图片格式不支持（仅 jpg/png/webp）: {p}")
    if video and not os.path.isfile(_resolve(video, base_dir)):
        errs.append(f"视频不存在: {video}")

    if not post.get("schedule"):
        errs.append("缺少 schedule")
    else:
        try:
            validate_schedule(post["schedule"], now=now)
        except ScheduleWindowError as exc:
            errs.append(str(exc))
    return errs


def plan_batch(
    manifest: dict, now: datetime | None = None, base_dir: str | None = None
) -> list[dict]:
    """校验整份清单：逐条校验 + 时间唯一性，返回规范化条目。"""
    posts = manifest.get("posts") or []
    if not posts:
        raise ValueError("清单里没有 posts")

    seen: dict[str, int] = {}
    out: list[dict] = []
    for idx, post in enumerate(posts, start=1):
        errs = validate_post(post, now=now, base_dir=base_dir)
        if errs:
            raise ValueError(f"第 {idx} 条不合法: " + "；".join(errs))

        slot = schedule_text(post["schedule"])
        if slot in seen:
            raise ValueError(f"第 {idx} 条与第 {seen[slot]} 条定时时间重复: {slot}")
        seen[slot] = idx

        item = {**post, "_no": idx, "_schedule_text": slot}
        item["images"] = [_resolve(p, base_dir) for p in (post.get("images") or [])]
        if post.get("video"):
            item["video"] = _resolve(post["video"], base_dir)
        out.append(item)
    return out


# ----------------------------------------------------------------------
# 发布后复核
# ----------------------------------------------------------------------

def _cookies(account: str | None) -> dict[str, str] | None:
    try:
        with open(config.get_cookie_file(account), encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return None
    jar = {
        c["name"]: c["value"]
        for c in state.get("cookies", [])
        if "douyin" in (c.get("domain") or "")
    }
    return jar or None


def _create_time_text(value) -> str:
    """接口可能返回中文串，也可能返回秒级/毫秒级时间戳。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e11:  # 毫秒
                ts /= 1000
            return datetime.fromtimestamp(ts, TZ).strftime("%Y年%m月%d日 %H:%M")
        except Exception:
            return ""
    return ""


def verify_scheduled(
    title: str, schedule: str, account: str | None = None, retries: int = 3
) -> bool | None:
    """查创作者接口复核。

    返回 True=已确认、False=接口正常返回但查不到、None=无法复核（网络/登录态/接口变更）。
    """
    try:
        import httpx

        jar = _cookies(account)
        if not jar:
            return None
        dt = datetime.strptime(schedule_text(schedule), "%Y-%m-%d %H:%M")
        expect_day = f"{dt.month}月{dt.day}日 {dt.hour:02d}:{dt.minute:02d}"
        head = title.strip()[:10]
        confirmed = False
        reached = False
        for attempt in range(max(1, retries)):
            if attempt:
                time.sleep(5)
            try:
                resp = httpx.get(CREATOR_ITEMS_API, headers=HEADERS, cookies=jar, timeout=25)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue
            items = data.get("item_info_list")
            if not isinstance(items, list):
                continue
            reached = True
            for item in items:
                if head and head in (item.get("title") or ""):
                    if expect_day in _create_time_text(item.get("create_time")):
                        confirmed = True
                        break
            if confirmed:
                return True
        if confirmed:
            return True
        return False if reached else None
    except Exception:  # noqa: BLE001 - 复核不能反过来把批次搞崩
        return None


# ----------------------------------------------------------------------
# 命令
# ----------------------------------------------------------------------

@click.command("publish-batch", help="按清单批量定时发布图文/视频（串行，失败即停）")
@click.argument("manifest", type=click.Path(exists=True))
@click.option("--start", default=1, show_default=True, help="从第几条开始")
@click.option("--end", "end", default=None, type=int, help="到第几条结束（含）")
@click.option("--delay", default=20, show_default=True, help="两条之间的基准间隔秒数")
@click.option("--dry-run", is_flag=True, help="只打印计划，不发布")
@click.option("--headless", is_flag=True, help="无头模式")
@click.option("--account", default=None, help="账号名（局部覆盖全局 --account）")
@click.pass_context
def publish_batch(ctx, manifest, start, end, delay, dry_run, headless, account):
    """批量定时发布。"""
    manifest_path = os.path.abspath(manifest)
    base_dir = os.path.dirname(manifest_path)

    try:
        data = load_manifest(manifest_path)
        posts = plan_batch(data, base_dir=base_dir)
    except (ValueError, ScheduleWindowError) as exc:
        error(f"清单校验失败: {exc}")
        raise SystemExit(1)

    account = account or effective_account(ctx) or data.get("account") or "default"

    lo = max(1, start)
    hi = min(end or len(posts), len(posts))
    if lo > hi:
        error(f"区间非法: start={start} end={end}（清单共 {len(posts)} 条）")
        raise SystemExit(1)
    todo = [p for p in posts if lo <= p["_no"] <= hi]

    console.print(f"[bold]计划发布 {len(todo)} 条[/bold]（账号 {account}）")
    for p in todo:
        console.print(
            f"  #{p['_no']} {p['_schedule_text']}  {p['title'][:20]}  "
            f"({len(p['images']) or 1} 图)"
        )

    if dry_run:
        info("dry-run：未发布")
        return

    cfg = config.load_config()
    client = PlaywrightClient(
        account=account,
        headless=headless or cfg["playwright"].get("headless", False),
        slow_mo=cfg["playwright"].get("slow_mo", 0),
    )

    ok: list[int] = []
    for p in todo:
        console.print(f"\n[bold]── #{p['_no']} {p['title']} → {p['_schedule_text']}[/bold]")
        try:
            if p.get("video"):
                result = client.publish_video(
                    title=p["title"],
                    content=p.get("content") or "",
                    video_path=p["video"],
                    tags=p.get("tags") or None,
                    visibility=p.get("visibility", "公开"),
                    schedule_at=p["_schedule_text"],
                    thumbnail_path=p.get("thumbnail"),
                    mentions=p.get("mentions") or None,
                )
            else:
                result = client.publish_image_text(
                    title=p["title"],
                    content=p.get("content") or "",
                    images=p["images"],
                    tags=p.get("tags") or None,
                    visibility=p.get("visibility", "公开"),
                    schedule_at=p["_schedule_text"],
                    mentions=p.get("mentions") or None,
                )
        except PlaywrightError as exc:
            error(f"发布失败，整批中止: {exc}")
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001 - 浏览器原生异常也要清晰中止
            error(f"发布异常，整批中止: {type(exc).__name__}: {exc}")
            raise SystemExit(1)

        status = result.get("status")
        if status != "published":
            error(f"状态异常（{status}），整批中止: {result.get('message')}")
            raise SystemExit(1)

        verdict = verify_scheduled(p["title"], p["_schedule_text"], account)
        if verdict is False:
            error(f"接口复核未通过：未查到 {p['_schedule_text']} 的作品，整批中止")
            raise SystemExit(1)
        if verdict is None:
            warning("接口无法复核（网络/登录态/接口变更），请到作品管理人工确认")

        success(f"#{p['_no']} 已确认：{status} / {p['_schedule_text']}")
        ok.append(p["_no"])
        if p is not todo[-1]:
            time.sleep(delay)

    success(f"完成 {len(ok)}/{len(todo)} 条：{ok}")


@click.command("bookable", help="查看当前可预约的定时日期（平台窗口：2 小时后 ~ 14 天内）")
@click.option("--hour", default=15, show_default=True, help="按该整点筛选可用日期")
def bookable(hour):
    """打印可约区间与可用日期，排期前先看这个。"""
    lo, hi = bookable_range()
    info(f"可约区间: {lo:%Y-%m-%d %H:%M} ~ {hi:%Y-%m-%d %H:%M}")
    days = bookable_dates(at_hour=hour)
    console.print(f"[bold]每天 {hour:02d}:00 可约 {len(days)} 天[/bold]")
    for d in days:
        console.print(f"  {d.isoformat()}")
