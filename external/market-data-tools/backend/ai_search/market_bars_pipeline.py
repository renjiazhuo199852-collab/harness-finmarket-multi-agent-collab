"""``market_bars`` 独立日线查询路线的在线编排。

本模块与 ``latest_price_pipeline`` 保持业务边界独立，只复用共享查询解析、工具检索、
数据集检索和字段目录读取能力。当前路线只查询 source 中已有的 ``daily`` 原始 K 线，
不对月、季、年数据做隐式聚合。
"""

from __future__ import annotations

from datetime import date
import os
from typing import Any

from .market_bar_request import parse_market_bar_request
from .market_bars_adapter import (
    MARKET_BAR_FIELDS,
    MARKET_BARS_FREQUENCY,
    MARKET_BARS_TABLE,
    query_market_bars,
)
from .query_intent import QueryRoute, recognize_query_intent
from .resolve_dataset_fields import resolve_dataset_fields
from .search_datasets import search_dataset_documents
from .search_instruments import _run_traced, search_instrument_documents


MARKET_BAR_DOWNSTREAM_STAGES = (
    "market_bar_request",
    "exact_match",
    "keyword_search",
    "pg_trgm_search",
    "embedding_search",
    "rrf_merge",
    "instrument_master",
    "candidate_selector",
    "instrument_identifier",
    "dataset_exact_match",
    "dataset_keyword_search",
    "dataset_pg_trgm_search",
    "dataset_embedding_search",
    "dataset_rrf_merge",
    "dataset_catalog",
    "dataset_candidate_selector",
    "dataset_field_catalog",
    "market_bars_query",
)
MARKET_BARS_CATALOG_CONTEXT = "OHLCV market bars daily"


def _market_route_guard(
    recognized_route: QueryRoute,
    requested_route: QueryRoute,
) -> dict[str, Any]:
    """确认当前页面只能执行 ``market_bars`` 路线。"""

    if requested_route != QueryRoute.MARKET_BARS:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "当前后端请求不是 market_bars 路线",
        }
    if recognized_route != requested_route:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "用户问题属于其他业务路线，已停止 market_bars 查询",
        }
    return {
        "accepted": True,
        "requested_route": requested_route.value,
        "recognized_route": recognized_route.value,
        "reason": "意图与当前 market_bars 路线一致，可以继续",
    }


def _skip_stage(
    trace_callback: Any,
    stage: str,
    stage_input: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """记录前置条件不满足而跳过的阶段，避免前端显示为无状态等待。"""

    output = {"status": "skipped", "reason": reason}
    if trace_callback is not None:
        trace_callback(
            {
                "stage": stage,
                "status": "skipped",
                "input": stage_input,
                "output": output,
                "duration_ms": 0,
                "error": None,
            }
        )
    return output


def _skip_stages(
    trace_callback: Any,
    stages: tuple[str, ...],
    reason: str,
) -> None:
    """批量补齐未执行阶段的 skipped 事件。"""

    for stage in stages:
        _skip_stage(trace_callback, stage, {}, reason)


def _stopped_result(
    query: str,
    requested_route: QueryRoute,
    query_intent: dict[str, Any],
    route_guard: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """构造路线被闸门或请求参数停止时的统一结果。"""

    return {
        "query": query,
        "route": requested_route.value,
        "query_intent": query_intent,
        "route_guard": route_guard,
        "market_bar_request": None,
        "methods": {},
        "warnings": [reason],
        "master_resolution": {"resolved": 0, "inactive": 0, "not_found": 0},
        "candidates": [],
        "model_selection": None,
        "identifier_resolution": None,
        "dataset_search": None,
        "dataset_resolution": None,
        "field_resolution": None,
        "market_bars_result": {"status": "skipped", "rows": [], "reason": reason},
        "price_result": None,
    }


def search_market_bars_route(
    cursor: Any,
    query: str,
    limit: int = 3,
    row_limit: int = 100,
    use_embedding: bool = True,
    use_candidate_llm: bool = True,
    provider: str | None = None,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    requested_route: QueryRoute = QueryRoute.MARKET_BARS,
    trace_callback: Any = None,
    query_intent_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行 ``market_bars`` 日线查询，只有所有目录条件通过才访问业务表。"""

    if not isinstance(requested_route, QueryRoute):
        requested_route = QueryRoute(requested_route)

    # 统一入口传入已经校验过的意图对象时不再重复调用查询解析模型。
    query_intent = query_intent_override or _run_traced(
        trace_callback,
        "query_parse",
        {
            "query": query,
            "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            "allowed_routes": [route.value for route in QueryRoute],
            "output_fields": [
                "route",
                "instrument_text",
                "provider_text",
                "time_expression",
                "request_text",
            ],
        },
        lambda: recognize_query_intent(query),
        lambda value: value,
    )
    recognized_route = QueryRoute(query_intent["route"])
    route_guard = _run_traced(
        trace_callback,
        "route_guard",
        {
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
        },
        lambda: _market_route_guard(recognized_route, requested_route),
        lambda value: value,
    )
    if not route_guard["accepted"]:
        reason = route_guard["reason"]
        _skip_stages(trace_callback, MARKET_BAR_DOWNSTREAM_STAGES, reason)
        return _stopped_result(query, requested_route, query_intent, route_guard, reason)

    # 模型会把“最近一个月”放在 time_expression，把“日 K 线/月 K 线”放在
    # request_text。参数解析器必须同时收到两部分；只传时间片段会让“月 K 线”
    # 退化为空文本，从而在真正执行前抛出 500，而不是返回受控的 unsupported。
    market_request_query = " ".join(
        part
        for part in (
            query_intent.get("time_expression"),
            query_intent.get("request_text"),
        )
        if part
    ).strip() or query
    market_bar_request = _run_traced(
        trace_callback,
        "market_bar_request",
        {
            "query": query,
            "parser_query": market_request_query,
            "start_date_override": start_date_override.isoformat() if start_date_override else None,
            "end_date_override": end_date_override.isoformat() if end_date_override else None,
            "row_limit": row_limit,
            "supported_frequency": MARKET_BARS_FREQUENCY,
        },
        lambda: parse_market_bar_request(
            market_request_query,
            start_date_override=start_date_override,
            end_date_override=end_date_override,
            row_limit=row_limit,
        ),
        lambda value: value,
    )
    if market_bar_request.get("status") != "resolved":
        reason = str(market_bar_request.get("reason") or "market_bars 请求参数未通过校验")
        _skip_stages(trace_callback, MARKET_BAR_DOWNSTREAM_STAGES[1:], reason)
        return {
            **_stopped_result(query, requested_route, query_intent, route_guard, reason),
            "market_bar_request": market_bar_request,
        }

    instrument_query = query_intent.get("instrument_text")
    instrument_search_query = query_intent.get("instrument_search_text") or instrument_query
    if not instrument_query or not instrument_search_query:
        reason = "查询解析没有提取到金融工具主体，已停止工具检索"
        _skip_stages(trace_callback, MARKET_BAR_DOWNSTREAM_STAGES[1:], reason)
        stopped = {
            **_stopped_result(query, requested_route, query_intent, route_guard, reason),
            "market_bar_request": market_bar_request,
            "instrument_query": None,
            "instrument_search_query": None,
            "dataset_query": None,
        }
        return stopped

    # UI 参数优先；如果 UI 没有填写，则使用模型从原文摘录出的供应商文本。
    query_provider = provider or query_intent.get("provider_text")

    result = search_instrument_documents(
        cursor,
        instrument_search_query,
        limit=limit,
        use_embedding=use_embedding,
        use_candidate_llm=use_candidate_llm,
        provider=query_provider,
        # 历史查询按结束日期校验标识，避免使用尚未在历史时点生效的标识。
        identifier_as_of_date=date.fromisoformat(market_bar_request["end_date"]),
        trace_callback=trace_callback,
    )
    warnings = list(result.get("warnings") or [])
    identifier_resolution = result.get("identifier_resolution")

    if identifier_resolution and identifier_resolution.get("status") == "resolved":
        selected_identifier = identifier_resolution.get("selected") or {}
        dataset_provider = selected_identifier.get("provider") or query_provider
        # 工具检索和数据集检索使用不同的文本片段。这里追加路线已确认的受控
        # 英文语义词，帮助英文数据集目录理解中文“日 K 线”，但不让模型猜表名。
        request_text = query_intent.get("request_text") or "historical daily OHLCV"
        dataset_query = f"{request_text} {MARKET_BARS_CATALOG_CONTEXT}".strip()
        result["dataset_search"] = search_dataset_documents(
            cursor,
            dataset_query,
            limit=limit,
            use_embedding=use_embedding,
            use_candidate_llm=use_candidate_llm,
            provider=query_provider,
            expected_provider=dataset_provider,
            trace_callback=trace_callback,
        )
        result["dataset_resolution"] = result["dataset_search"].get("dataset_resolution")
    else:
        reason = "没有唯一有效的 instrument_identifier，已跳过数据集目录检索"
        result["dataset_search"] = {
            "status": "skipped",
            "provider_requested": query_provider,
            "provider_expected": None,
            "warnings": [reason],
            "methods": {},
            "catalog_resolution": {},
            "candidates": [],
            "model_selection": None,
            "dataset_resolution": {
                "status": "skipped",
                "dataset_id": None,
                "storage_table_name": None,
                "provider": None,
                "frequency": None,
                "reason": reason,
            },
        }
        result["dataset_resolution"] = result["dataset_search"]["dataset_resolution"]

    dataset_resolution = result.get("dataset_resolution") or {}
    if dataset_resolution.get("status") != "resolved":
        reason = "数据集目录未 resolved，已跳过字段目录和 market_bars 查询"
        _skip_stage(trace_callback, "dataset_field_catalog", {}, reason)
        _skip_stage(trace_callback, "market_bars_query", {}, reason)
        result["field_resolution"] = {
            "status": "skipped",
            "dataset_id": dataset_resolution.get("dataset_id"),
            "storage_table_name": dataset_resolution.get("storage_table_name"),
            "requested_fields": list(MARKET_BAR_FIELDS),
            "fields": [],
            "reason": reason,
        }
        result["market_bars_result"] = {"status": "skipped", "rows": [], "reason": reason}
    elif (
        dataset_resolution.get("storage_table_name") != MARKET_BARS_TABLE
        or dataset_resolution.get("frequency") != MARKET_BARS_FREQUENCY
    ):
        reason = "数据集不是当前支持的 daily market_bars 数据集，已停止查询"
        warnings.append(reason)
        _skip_stage(trace_callback, "dataset_field_catalog", {}, reason)
        _skip_stage(trace_callback, "market_bars_query", {}, reason)
        result["field_resolution"] = {
            "status": "skipped",
            "dataset_id": dataset_resolution.get("dataset_id"),
            "storage_table_name": dataset_resolution.get("storage_table_name"),
            "requested_fields": list(MARKET_BAR_FIELDS),
            "fields": [],
            "reason": reason,
        }
        result["market_bars_result"] = {
            "status": "unsupported_frequency",
            "rows": [],
            "reason": reason,
        }
    else:
        result["field_resolution"] = _run_traced(
            trace_callback,
            "dataset_field_catalog",
            {
                "dataset_id": dataset_resolution.get("dataset_id"),
                "storage_table_name": dataset_resolution.get("storage_table_name"),
                "requested_fields": list(MARKET_BAR_FIELDS),
                "selection_mode": "market_bars_daily_route_policy",
                "llm": False,
            },
            lambda: resolve_dataset_fields(
                cursor,
                dataset_resolution["dataset_id"],
                dataset_resolution["storage_table_name"],
                list(MARKET_BAR_FIELDS),
            ),
            lambda value: value,
        )
        if result["field_resolution"].get("status") == "resolved":
            selected_identifier = identifier_resolution.get("selected") or {}
            selected_tool = result.get("model_selection") or {}
            result["market_bars_result"] = _run_traced(
                trace_callback,
                "market_bars_query",
                {
                    "instrument_id": selected_tool.get("instrument_id"),
                    "provider": selected_identifier.get("provider"),
                    "identifier": selected_identifier.get("identifier"),
                    "dataset_id": dataset_resolution.get("dataset_id"),
                    "storage_table_name": dataset_resolution.get("storage_table_name"),
                    "frequency": MARKET_BARS_FREQUENCY,
                    "start_date": market_bar_request["start_date"],
                    "end_date": market_bar_request["end_date"],
                    "fields": [
                        field.get("physical_column_name")
                        for field in result["field_resolution"].get("fields", [])
                    ],
                    "order_by": "date ASC",
                    "limit": market_bar_request["row_limit"],
                },
                lambda: query_market_bars(
                    cursor,
                    selected_tool["instrument_id"],
                    selected_identifier["provider"],
                    selected_identifier["identifier"],
                    dataset_resolution,
                    result["field_resolution"],
                    date.fromisoformat(market_bar_request["start_date"]),
                    date.fromisoformat(market_bar_request["end_date"]),
                    frequency=MARKET_BARS_FREQUENCY,
                    limit=market_bar_request["row_limit"],
                ),
                lambda value: value,
            )
        else:
            reason = "字段目录或物理列校验未通过，已停止 market_bars 查询"
            _skip_stage(trace_callback, "market_bars_query", {}, reason)
            result["market_bars_result"] = {"status": "skipped", "rows": [], "reason": reason}

    # 工具检索内部使用 instrument_text；顶层结果仍保留用户原始问题，并把两个
    # 实际送入检索模块的文本分开记录，方便前端检查解析是否正确。
    result["query"] = query
    result["instrument_query"] = instrument_query
    result["instrument_search_query"] = instrument_search_query
    result["dataset_query"] = (
        result.get("dataset_search", {}).get("query")
        if isinstance(result.get("dataset_search"), dict)
        else None
    )
    result["warnings"] = warnings
    result["route"] = requested_route.value
    result["query_intent"] = query_intent
    result["route_guard"] = route_guard
    result["market_bar_request"] = market_bar_request
    result["price_result"] = None
    return result
