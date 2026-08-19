"""五个 Agent 工具的公共 Python 接口和注册表。

本模块是 tools 项目对外的主要边界：调用方只需要提供自然语言和业务过滤条件。
内部仍然执行数据集目录发现、金融工具确认、字段目录解析和安全适配器查询，
但不会让调用方传入表名、字段名、SQL 或固定路线枚举。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

import psycopg2

from .config import database_connection_kwargs
from .env_config import load_project_env
from .public_response import build_public_error, build_public_response
from .unified_search_pipeline import run_unified_query


TraceCallback = Callable[[dict[str, Any]], None]

_ROUTE_TABLES = {
    "latest_prices_search": ("latest_prices", "latest_prices"),
    "market_bars_search": ("market_bars", "market_bars"),
    "macro_observations_search": ("macro_observations", "macro_observations"),
    "news_articles_search": ("news_articles", "news_articles"),
}


def _date_value(value: date | str | None) -> date | None:
    """把 Agent 或 HTTP 输入的 ISO 日期统一为 date 对象。"""

    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _clean_provider(provider: str | None) -> str | None:
    """清理供应商过滤值；没有指定时始终保持 None。"""

    clean = provider.strip() if provider else ""
    return clean or None


def run_tool_internal(
    name: str,
    *,
    query: str,
    provider: str | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    max_rows: int = 100,
    trace_callback: TraceCallback | None = None,
) -> dict[str, Any]:
    """执行工具并返回完整内部结果，供精简接口和调试 SSE 共同使用。"""

    load_project_env()
    clean_query = query.strip()
    if not clean_query:
        raise ValueError("query 不能为空")
    if max_rows < 1 or max_rows > 1000:
        raise ValueError("max_rows 必须在 1 到 1000 之间")

    route = _ROUTE_TABLES.get(name)
    compatibility_route = route[0] if route else None
    expected_table = route[1] if route else None

    with psycopg2.connect(**database_connection_kwargs()) as connection:
        # AI Search 只读 source 和 ai_search，工具接口不允许修改业务数据库。
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            return run_unified_query(
                cursor,
                clean_query,
                limit=3,
                row_limit=max_rows,
                provider=_clean_provider(provider),
                use_embedding=True,
                use_candidate_llm=True,
                start_date_override=_date_value(start_date),
                end_date_override=_date_value(end_date),
                trace_callback=trace_callback,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_table,
            )


def _public_call(name: str, **arguments: Any) -> dict[str, Any]:
    """执行一个工具并转换为稳定的 status + data 公开协议。"""

    try:
        result = run_tool_internal(name, **arguments)
        return build_public_response(result)
    except Exception as exc:  # noqa: BLE001 - 工具必须返回结构化错误
        return build_public_error(type(exc).__name__, str(exc))


def unified_search(
    query: str,
    provider: str | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    max_rows: int = 100,
) -> dict[str, Any]:
    """根据 dataset_catalog 自动发现数据集并执行统一自然语言查询。"""

    return _public_call(
        "unified_search",
        query=query,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        max_rows=max_rows,
    )


def latest_prices_search(query: str, provider: str | None = None) -> dict[str, Any]:
    """查询金融工具的最新价格。"""

    return _public_call("latest_prices_search", query=query, provider=provider)


def market_bars_search(
    query: str,
    provider: str | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    max_rows: int = 100,
) -> dict[str, Any]:
    """查询日线 OHLCV 历史行情。"""

    return _public_call(
        "market_bars_search",
        query=query,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        max_rows=max_rows,
    )


def macro_observations_search(
    query: str,
    provider: str | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    max_rows: int = 100,
) -> dict[str, Any]:
    """查询宏观指标观测值。"""

    return _public_call(
        "macro_observations_search",
        query=query,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        max_rows=max_rows,
    )


def news_articles_search(
    query: str,
    provider: str | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> dict[str, Any]:
    """查询与用户问题文本或语义相关的新闻，不限制最终新闻候选条数。"""

    return _public_call(
        "news_articles_search",
        query=query,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        max_rows=1000,
    )


def get_tool_definitions() -> list[dict[str, Any]]:
    """返回可直接交给 Agent 的 OpenAI 兼容函数工具定义。"""

    query = {"type": "string", "description": "用户的自然语言查询问题"}
    provider = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": "可选数据供应商；用户未指定时传 null",
    }
    date_field = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": "可选 ISO 日期，例如 2026-08-01",
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "unified_search",
                "description": "从数据集目录自动发现并查询金融价格、历史行情、宏观指标或新闻",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": query,
                        "provider": provider,
                        "start_date": date_field,
                        "end_date": date_field,
                        "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "latest_prices_search",
                "description": "查询金融工具最新价格",
                "parameters": {
                    "type": "object",
                    "properties": {"query": query, "provider": provider},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "market_bars_search",
                "description": "查询日线 OHLCV 历史行情",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": query,
                        "provider": provider,
                        "start_date": date_field,
                        "end_date": date_field,
                        "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "macro_observations_search",
                "description": "查询宏观指标观测值",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": query,
                        "provider": provider,
                        "start_date": date_field,
                        "end_date": date_field,
                        "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "news_articles_search",
                "description": "查询与问题相关的新闻文章，不限制最终候选数量",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": query,
                        "provider": provider,
                        "start_date": date_field,
                        "end_date": date_field,
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def invoke_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """按工具名称调用函数，供 Agent 框架使用。"""

    functions = {
        "unified_search": unified_search,
        "latest_prices_search": latest_prices_search,
        "market_bars_search": market_bars_search,
        "macro_observations_search": macro_observations_search,
        "news_articles_search": news_articles_search,
    }
    function = functions.get(name)
    if function is None:
        return build_public_error("TOOL_NOT_FOUND", f"未知工具：{name}")
    try:
        return function(**arguments)
    except TypeError as exc:
        return build_public_error("INVALID_TOOL_ARGUMENTS", str(exc))
