from __future__ import annotations

from datetime import date, datetime, timedelta

import pytz

_WEEKDAYS_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_DEFAULT_TIMEZONE = "Asia/Shanghai"


def _resolve_now(timezone: str, now: datetime | None) -> datetime:
    tz = pytz.timezone(timezone)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return tz.localize(now)
    return now.astimezone(tz)


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _next_month_start(day: date) -> date:
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1, day=1)
    return day.replace(month=day.month + 1, day=1)


def build_data_query_time_anchor_block(
    timezone: str = _DEFAULT_TIMEZONE,
    now: datetime | None = None,
) -> str:
    """Build a deterministic time anchor block for ChatBI / Data Agent prompts."""
    resolved = _resolve_now(timezone, now)
    today = resolved.date()
    weekday_str = _WEEKDAYS_CN[today.weekday()]
    now_str = resolved.strftime(f"%Y-%m-%d %H:%M:%S {weekday_str}")

    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    last_week_end = week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    month_start = _month_start(today)
    month_end = _next_month_start(today) - timedelta(days=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = _month_start(last_month_end)

    year_start = today.replace(month=1, day=1)
    last_year_start = today.replace(year=today.year - 1, month=1, day=1)
    last_year_end = today.replace(year=today.year - 1, month=12, day=31)

    return (
        "[当前时间锚点]\n"
        f"- 基准时区：{timezone}\n"
        f"- 当前时刻：{now_str}\n"
        f"- 今天：{today.isoformat()}\n"
        f"- 昨天：{yesterday.isoformat()}\n"
        f"- 本周（周一至今天）：{week_start.isoformat()} 至 {today.isoformat()}\n"
        f"- 上周（周一至周日）：{last_week_start.isoformat()} 至 {last_week_end.isoformat()}\n"
        f"- 本月/当月（月初至今天）：{month_start.isoformat()} 至 {today.isoformat()}\n"
        f"- 本月完整自然月：{month_start.isoformat()} 至 {month_end.isoformat()}\n"
        f"- 上月：{last_month_start.isoformat()} 至 {last_month_end.isoformat()}\n"
        f"- 今年（年初至今天）：{year_start.isoformat()} 至 {today.isoformat()}\n"
        f"- 去年：{last_year_start.isoformat()} 至 {last_year_end.isoformat()}\n"
        "\n"
        "【相对时间 SQL 规则】\n"
        "用户问题含「今天/昨天/本周/上周/本月/当月/上月/今年/去年」等相对时间时：\n"
        "1. execute_sql_query 的 time_window 必须与以上锚点日期一致，禁止臆测年份或月份。\n"
        "2. 「本月/当月」默认按「月初至今（含今天）」过滤；若用户明确要求整月，使用「本月完整自然月」起止日。\n"
        "3. 在 <sql_plan> 的 time_window 中写出具体起止日期（YYYY-MM-DD），不得只写「本月」等模糊词。"
    )
