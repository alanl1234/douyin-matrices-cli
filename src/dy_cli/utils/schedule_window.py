"""抖音定时发布窗口校验。

平台规则（creator.douyin.com 发布页原文）：
    「支持 2 小时后及 14 天内的定时发布」

本模块只放纯函数，便于单测；不做任何网络/浏览器操作。
所有比较统一换算到 Asia/Shanghai，避免 aware/naive 混用导致的 8 小时错位。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# 中国无夏令时（1991 年起），固定 +08:00 即可，避免依赖 tzdata
TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

MIN_LEAD = timedelta(hours=2)
MAX_LEAD = timedelta(days=14)

_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y年%m月%d日 %H:%M",  # 平台输入框的中文回显形态
    "%Y年%m月%d日 %H:%M:%S",
)


class ScheduleWindowError(ValueError):
    """定时时间不合法或超出平台允许窗口。"""


def parse_schedule(value: str | datetime) -> datetime:
    """解析 ISO8601 / 常见格式 / YAML 原生 datetime。"""
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise ScheduleWindowError("定时时间不能为空")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    for fmt in _FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ScheduleWindowError(f"无法解析定时时间: {value!r}")


def to_local(dt: datetime) -> datetime:
    """换算到 Asia/Shanghai；naive 视为本地时间。"""
    return dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)


def schedule_text(value: str | datetime) -> str:
    """统一成平台输入框格式 ``YYYY-MM-DD HH:MM``（本地时区）。"""
    return to_local(parse_schedule(value)).strftime("%Y-%m-%d %H:%M")


def _minute(dt: datetime) -> datetime:
    """秒级抖动不算差异，统一截到分钟。"""
    dt = to_local(dt)
    return dt.replace(second=0, microsecond=0)


def validate_schedule(value: str | datetime, now: datetime | None = None) -> datetime:
    """校验定时时间落在 [now+2h, now+14d]；不合法则抛 ScheduleWindowError。

    返回换算到本地时区、截到分钟的时间。
    """
    dt = _minute(parse_schedule(value))
    ref = _minute(now) if now else _minute(datetime.now(TZ))
    earliest = ref + MIN_LEAD
    latest = ref + MAX_LEAD
    if dt < earliest:
        raise ScheduleWindowError(
            f"定时时间过早：{dt:%Y-%m-%d %H:%M} 需不早于 {earliest:%Y-%m-%d %H:%M}（平台要求 2 小时后）"
        )
    if dt > latest:
        raise ScheduleWindowError(
            f"定时时间超出平台窗口：{dt:%Y-%m-%d %H:%M} 需不晚于 {latest:%Y-%m-%d %H:%M}（平台最长 14 天）"
        )
    return dt


def bookable_range(now: datetime | None = None) -> tuple[datetime, datetime]:
    """返回当前可预约的时间区间 (最早, 最晚)。"""
    ref = _minute(now) if now else _minute(datetime.now(TZ))
    return ref + MIN_LEAD, ref + MAX_LEAD


def bookable_dates(at_hour: int = 15, now: datetime | None = None) -> list[date]:
    """返回窗口内所有可用的日期（含今天，只要 at_hour 时刻合法）。"""
    ref = _minute(now) if now else _minute(datetime.now(TZ))
    _, latest = bookable_range(ref)
    out: list[date] = []
    day = ref.date()
    while day <= latest.date():
        try:
            validate_schedule(datetime.combine(day, datetime.min.time(), TZ).replace(hour=at_hour), now=ref)
        except ScheduleWindowError:
            day += timedelta(days=1)
            continue
        out.append(day)
        day += timedelta(days=1)
    return out
