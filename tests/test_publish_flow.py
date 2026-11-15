"""发布链路的纯函数校验测试（不启动浏览器）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dy_cli.commands.publish_batch import (
    plan_batch,
    schedule_text,
    validate_post,
)
from dy_cli.engines.playwright_client import BGM_BLOCKLIST, BGM_SOFT_QUERIES
from dy_cli.utils.schedule_window import (
    ScheduleWindowError,
    bookable_dates,
    bookable_range,
    parse_schedule,
    validate_schedule,
)

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 14, 18, 0, tzinfo=TZ)


# ---------------------------------------------------------------- 解析/时区

def test_parse_accepts_iso_with_tz():
    dt = parse_schedule("2026-11-15T15:00:00+08:00")
    assert dt.hour == 15 and dt.tzinfo is not None


def test_parse_accepts_yaml_datetime():
    dt = datetime(2026, 11, 15, 15, 0)
    assert parse_schedule(dt) is dt


def test_parse_rejects_garbage():
    with pytest.raises(ScheduleWindowError):
        parse_schedule("明天下午三点")


def test_schedule_text_normalizes_utc_offset():
    # 07:00Z == 15:00+08:00，必须统一成北京时间
    assert schedule_text("2026-11-16T07:00:00+00:00") == "2026-11-16 15:00"


def test_schedule_text_accepts_seconds_and_naive():
    assert schedule_text("2026-11-16 15:00:30") == "2026-11-16 15:00"


# ---------------------------------------------------------------- 窗口校验

def test_schedule_too_soon_rejected():
    with pytest.raises(ScheduleWindowError):
        validate_schedule(NOW + timedelta(minutes=30), now=NOW)


def test_schedule_beyond_14_days_rejected():
    with pytest.raises(ScheduleWindowError):
        validate_schedule(NOW + timedelta(days=15), now=NOW)


def test_schedule_inside_window_ok():
    target = NOW + timedelta(days=1)
    assert validate_schedule(target, now=NOW) == target


def test_exactly_2h_boundary_allowed():
    assert validate_schedule(NOW + timedelta(hours=2), now=NOW)


def test_aware_dt_with_naive_now_still_correct():
    """now 传 naive 也不能把带时区的目标判错 8 小时。"""
    target = "2026-11-16T15:00:00+08:00"
    naive_now = datetime(2026, 11, 16, 9, 0)  # 本地 11-16 09:00，目标还有 6h
    assert validate_schedule(target, now=naive_now)


def test_utc_value_not_treated_as_local():
    # 11-16 07:00Z = 15:00 北京，距 11-14 18:00 还有 45h，应通过
    assert validate_schedule("2026-11-16T07:00:00+00:00", now=NOW)


def test_bookable_range_is_2h_to_14d():
    lo, hi = bookable_range(NOW)
    assert lo == NOW + timedelta(hours=2)
    assert hi == NOW + timedelta(days=14)


def test_bookable_dates_counts_today_only_if_late_enough():
    days = bookable_dates(at_hour=15, now=NOW)  # 18:00 时当天 15:00 已过
    assert NOW.date() not in days
    assert (NOW + timedelta(days=1)).date() in days
    assert len(days) <= 14


# ---------------------------------------------------------------- 清单校验

@pytest.fixture
def png(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")  # 只要求存在 + 扩展名合法
    return str(p)


def _post(png_path, **over):
    base = {
        "title": "阻力越小滑得越远，然后呢？",
        "content": "正文",
        "images": [png_path],
        "tags": ["初中物理"],
        "schedule": "2026-11-16T15:00:00+08:00",
    }
    base.update(over)
    return base


def test_validate_post_ok(png):
    assert validate_post(_post(png), now=NOW) == []


def test_validate_post_missing_title(png):
    assert any("title" in e for e in validate_post(_post(png, title=""), now=NOW))


def test_validate_post_title_too_long(png):
    assert any("20 字" in e for e in validate_post(_post(png, title="实" * 25), now=NOW))


def test_validate_post_missing_media():
    assert any("images" in e for e in validate_post(_post([], images=[]), now=NOW))


def test_validate_post_bad_extension(tmp_path):
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a")
    errs = validate_post(_post(str(gif)), now=NOW)
    assert any("格式不支持" in e for e in errs)


def test_validate_post_schedule_out_of_window(png):
    errs = validate_post(_post(png, schedule="2027-06-01T15:00:00+08:00"), now=NOW)
    assert errs and "14 天" in errs[0]


def test_plan_batch_rejects_duplicate_slots(png):
    manifest = {"posts": [
        _post(png),
        _post(png, title="另一条标题", schedule="2026-11-16 15:00:00"),
    ]}
    with pytest.raises(ValueError, match="重复"):
        plan_batch(manifest, now=NOW)


def test_plan_batch_normalizes_schedule_text(png):
    posts = plan_batch({"posts": [_post(png)]}, now=NOW)
    assert posts[0]["_schedule_text"] == "2026-11-16 15:00"
    assert posts[0]["_no"] == 1


def test_plan_batch_resolves_relative_path(tmp_path, png):
    manifest = {"posts": [_post("a.png")]}
    posts = plan_batch(manifest, now=NOW, base_dir=str(tmp_path))
    assert posts[0]["images"][0].endswith("a.png")


def test_plan_batch_rejects_empty():
    with pytest.raises(ValueError):
        plan_batch({"posts": []}, now=NOW)


# ---------------------------------------------------------------- BGM 过滤

def test_bgm_blocklist_catches_loud_tracks():
    loud = ["古嗦古嗦（DJ版）", "御姐进行曲", "爆了流量热歌", "大湾区进行曲 (纯净版)"]
    for t in loud:
        assert any(b in t for b in BGM_BLOCKLIST), t


def test_bgm_blocklist_keeps_soft_tracks():
    soft = ["轻音乐吴美琪", "纯音乐Qovc", "时光静好（钢琴曲）", "秋日私语"]
    for t in soft:
        assert not any(b in t for b in BGM_BLOCKLIST), t


def test_bgm_queries_are_soft_only():
    assert set(BGM_SOFT_QUERIES) == {"轻音乐", "纯音乐", "钢琴"}


def test_timezone_offsets_are_not_confused():
    # 同一时刻的两种写法必须相等
    a = validate_schedule("2026-11-16T15:00:00+08:00", now=NOW)
    b = validate_schedule("2026-11-16T07:00:00+00:00", now=NOW)
    assert a == b == datetime(2026, 11, 16, 15, 0, tzinfo=TZ)
    assert b.astimezone(timezone.utc).hour == 7
