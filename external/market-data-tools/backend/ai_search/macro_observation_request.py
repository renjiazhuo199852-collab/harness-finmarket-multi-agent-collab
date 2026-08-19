"""解析 ``macro_observations`` 路线的时间、频率和返回行数。

宏观数据的时间语义与 ``market_bars`` 不同：没有时间条件时默认查询最新一条
发布记录；用户明确给出日期或相对时间范围时，才查询历史发布记录。模块只负责
把用户原文中的时间条件转换成受控参数，不猜测数据集、字段名或 SQL。
"""

from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any


MACRO_DEFAULT_ROW_LIMIT = 30
MACRO_MAX_ROW_LIMIT = 1000
MACRO_FIELDS = (
    "value",
    "previous_value",
    "forecast_value",
    "revised_value",
)

_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_count(value: str) -> int | None:
    """将一个中文或阿拉伯数字转换成正整数。"""

    text = value.strip()
    if text.isdigit():
        number = int(text)
        return number if number > 0 else None
    return _CHINESE_DIGITS.get(text)


def _safe_date(year: str, month: str, day: str) -> date | None:
    """安全构造日期，非法日期返回 ``None`` 而不是让查询线程异常退出。"""

    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _next_month(value: date) -> date:
    """返回下一个月的第一天，用于构造半开日期区间。"""

    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _extract_dates(expression: str) -> list[date]:
    """提取常见的年月日、ISO 和斜线日期表达。"""

    pattern = re.compile(
        r"(?<!\d)(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*"
        r"(?:月|[-/])\s*(\d{1,2})\s*日?"
    )
    dates: list[date] = []
    for match in pattern.finditer(expression):
        parsed = _safe_date(*match.groups())
        if parsed is not None:
            dates.append(parsed)
    return dates


def _extract_year_or_month(expression: str) -> tuple[date, date] | None:
    """处理 ``2025 年`` 或 ``2025 年 6 月`` 这样的自然时间范围。"""

    month_match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", expression)
    if month_match:
        start = _safe_date(month_match.group(1), month_match.group(2), "1")
        if start:
            return start, _next_month(start)

    year_match = re.search(r"(20\d{2})\s*年", expression)
    if year_match:
        start = _safe_date(year_match.group(1), "1", "1")
        if start:
            return start, date(start.year + 1, 1, 1)
    return None


def _relative_start(expression: str, reference_date: date) -> date | None:
    """解析最近 N 天、周、月或年的开始日期。"""

    match = re.search(
        r"(?:最近|近|过去)\s*(\d+|[一二两三四五六七八九十])\s*"
        r"(?:个)?\s*(天|日|周|星期|月|年)",
        expression,
    )
    if not match:
        return None
    count = _parse_count(match.group(1))
    if count is None:
        return None
    unit = match.group(2)
    if unit in {"天", "日"}:
        days = count
    elif unit in {"周", "星期"}:
        days = count * 7
    elif unit == "月":
        days = count * 30
    else:
        days = count * 365
    return reference_date - timedelta(days=days)


def _parse_frequency(expression: str) -> str | None:
    """识别用户明确指定的宏观数据频率；没有指定时返回 ``None``。"""

    if re.search(r"季度|季频|quarter", expression, re.IGNORECASE):
        return "quarterly"
    if re.search(r"月度|月频|monthly", expression, re.IGNORECASE):
        return "monthly"
    if re.search(r"日度|日频|daily", expression, re.IGNORECASE):
        return "daily"
    return None


def parse_macro_observation_request(
    time_expression: str | None,
    request_text: str | None = None,
    *,
    reference_date: date | None = None,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    row_limit: int = MACRO_DEFAULT_ROW_LIMIT,
) -> dict[str, Any]:
    """将宏观查询的时间条件转换成受控查询参数。

    日期范围使用左闭右开语义：``start_date`` 包含当天，``end_date`` 表示结束日
    的下一天。这能覆盖带时分秒的 ``release_time``，也避免把一天的最后一条记录
    排除在外。没有日期条件时返回 ``latest``，由适配器按发布时间倒序取一条。
    """

    if row_limit < 1 or row_limit > MACRO_MAX_ROW_LIMIT:
        raise ValueError(
            f"macro_observations row_limit 必须在 1 到 {MACRO_MAX_ROW_LIMIT} 之间"
        )

    expression = " ".join(
        part.strip() for part in (time_expression or "", request_text or "") if part
    ).strip()
    frequency = _parse_frequency(expression)
    today = reference_date or date.today()
    start_date = start_date_override
    end_date = end_date_override

    if start_date is None and end_date is None:
        explicit_dates = _extract_dates(expression)
        period = _extract_year_or_month(expression)
        if period:
            start_date, end_date = period
        elif len(explicit_dates) >= 2:
            start_date, end_date = explicit_dates[0], explicit_dates[1] + timedelta(days=1)
        elif len(explicit_dates) == 1:
            start_date = explicit_dates[0]
            end_date = explicit_dates[0] + timedelta(days=1)
        else:
            relative_start = _relative_start(expression, today)
            if relative_start:
                start_date, end_date = relative_start, today + timedelta(days=1)
    elif start_date is None:
        start_date = end_date
    elif end_date is None:
        end_date = today + timedelta(days=1)

    if start_date is not None and end_date is not None and start_date >= end_date:
        return {
            "status": "invalid",
            "period_type": "invalid",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "frequency": frequency,
            "row_limit": row_limit,
            "requested_fields": list(MACRO_FIELDS),
            "reason": "宏观数据开始日期必须早于结束日期",
        }

    if start_date is None and end_date is None:
        return {
            "status": "resolved",
            "period_type": "latest",
            "start_date": None,
            "end_date": None,
            "frequency": frequency,
            "row_limit": min(row_limit, 1),
            "requested_fields": list(MACRO_FIELDS),
            "reason": "没有指定历史范围，按 release_time 返回最新一条记录",
        }

    return {
        "status": "resolved",
        "period_type": "history",
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "frequency": frequency,
        "row_limit": row_limit,
        "requested_fields": list(MACRO_FIELDS),
        "reason": "已解析为宏观数据发布时间范围查询",
    }
