"""解析 ``market_bars`` 路线的日期范围和频率约束。

``source.market_bars`` 使用同一张表保存日线和小时原始 K 线。4H 请求返回
``hourly`` 原始频率，后续由 FX evidence factory 聚合成完整 4H；本模块只解析
频率和日期，不猜测表名、列名或 SQL。
"""

from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any


SUPPORTED_FREQUENCY = "daily"
INTRADAY_FREQUENCY = "hourly"
DEFAULT_RANGE_DAYS = 30
MAX_ROW_LIMIT = 1000

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
    """把中文或阿拉伯数字转换为正整数；无法识别时返回 ``None``。"""

    text = value.strip()
    if text.isdigit():
        count = int(text)
        return count if count > 0 else None
    return _CHINESE_DIGITS.get(text)


def _parse_date(year: str, month: str, day: str) -> date | None:
    """把正则捕获的年月日安全转换成日期，非法日期不向后传播。"""

    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _extract_explicit_dates(query: str) -> list[date]:
    """识别 ``YYYY-MM-DD``、``YYYY/MM/DD`` 和中文年月日表达。"""

    pattern = re.compile(
        r"(?<!\d)(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*"
        r"(?:月|[-/])\s*(\d{1,2})\s*日?"
    )
    parsed: list[date] = []
    for match in pattern.finditer(query):
        parsed_date = _parse_date(*match.groups())
        if parsed_date is not None:
            parsed.append(parsed_date)
    return parsed


def _relative_start_date(query: str, reference_date: date) -> date | None:
    """解析“最近 N 天/月/年”等相对范围的开始日期。"""

    match = re.search(
        r"(?:最近|近|过去)\s*(\d+|[一二两三四五六七八九十])\s*"
        r"(?:个)?\s*(天|日|周|星期|月|年)",
        query,
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
        # 当前路线按自然日过滤日线数据，先用 30 天表示一个查询月。
        days = count * 30
    else:
        # 年份只用于计算日期范围，不代表年频率 K 线。
        days = count * 365
    return reference_date - timedelta(days=days)


def _requested_frequency(query: str) -> str:
    """识别日内 K 线请求；4H/1H 使用 hourly 原始数据。"""

    if re.search(
        r"(?:\b(?:1|4)\s*[hH]\b|小时|hourly|intraday|日内)",
        query,
        re.IGNORECASE,
    ):
        return INTRADAY_FREQUENCY
    return SUPPORTED_FREQUENCY


def _unsupported_period(query: str) -> str | None:
    """识别当前原始数据不支持的月、季或年 K 线请求。"""

    if re.search(r"月\s*(?:K|k|线)|月度\s*(?:K|k|线)", query):
        return "当前 market_bars 没有月线原始数据，暂不支持月 K 线"
    if re.search(r"季\s*(?:K|k|线)|季度\s*(?:K|k|线)", query):
        return "当前 market_bars 没有季线原始数据，暂不支持季 K 线"
    if re.search(r"年\s*(?:K|k|线)|年度\s*(?:K|k|线)", query):
        return "当前 market_bars 没有年线原始数据，暂不支持年 K 线"
    return None


def parse_market_bar_request(
    query: str,
    *,
    reference_date: date | None = None,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    row_limit: int = 100,
) -> dict[str, Any]:
    """将自然语言转换为受控的日线查询参数。

    明确的前端日期控件优先于自然语言日期；自然语言没有日期时使用最近 30 天。
    ``row_limit`` 只控制返回行数，不会改变候选工具或数据集的检索数量。
    """

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("查询文本不能为空")
    if row_limit < 1 or row_limit > MAX_ROW_LIMIT:
        raise ValueError(f"market_bars row_limit 必须在 1 到 {MAX_ROW_LIMIT} 之间")

    unsupported_reason = _unsupported_period(clean_query)
    if unsupported_reason:
        return {
            "status": "unsupported",
            "frequency": None,
            "period_type": "unsupported",
            "start_date": None,
            "end_date": None,
            "row_limit": row_limit,
            "reason": unsupported_reason,
        }

    today = reference_date or date.today()
    explicit_dates = _extract_explicit_dates(clean_query)
    start_date = start_date_override
    end_date = end_date_override

    if start_date is None and end_date is None:
        if len(explicit_dates) >= 2:
            start_date, end_date = explicit_dates[0], explicit_dates[1]
        elif len(explicit_dates) == 1:
            # 单个日期默认作为结束日期，向前查询一个默认窗口。
            end_date = explicit_dates[0]
            start_date = end_date - timedelta(days=DEFAULT_RANGE_DAYS)
        else:
            relative_start = _relative_start_date(clean_query, today)
            start_date = relative_start or today - timedelta(days=DEFAULT_RANGE_DAYS)
            end_date = today
    elif start_date is None:
        start_date = end_date - timedelta(days=DEFAULT_RANGE_DAYS) if end_date else None
    elif end_date is None:
        end_date = today

    if start_date is None or end_date is None:
        return {
            "status": "invalid",
            "frequency": None,
            "period_type": "invalid",
            "start_date": None,
            "end_date": None,
            "row_limit": row_limit,
            "reason": "无法确定有效的开始日期和结束日期",
        }
    if start_date > end_date:
        return {
            "status": "invalid",
            "frequency": None,
            "period_type": "invalid",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "row_limit": row_limit,
            "reason": "开始日期不能晚于结束日期",
        }

    frequency = _requested_frequency(clean_query)
    return {
        "status": "resolved",
        "frequency": frequency,
        "period_type": "hourly" if frequency == INTRADAY_FREQUENCY else "daily",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "row_limit": row_limit,
        "reason": (
            "已解析为小时原始 K 线日期范围查询"
            if frequency == INTRADAY_FREQUENCY
            else "已解析为日线日期范围查询"
        ),
    }
