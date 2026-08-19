"""latest_prices 独立路线的在线查询编排。

当前项目把四条业务路线拆开开发。本模块只编排最新价格路线：先调用共享查询解析
模型提取路线和工具主体，再校验当前页面是否允许执行，然后依次完成金融工具、供应商
标识和数据集目录确认，最后按字段目录和供应商标识查询 ``latest_prices`` 最新报价。
其他三条业务路线仍然不会进入本模块。
"""

from __future__ import annotations

import os
from typing import Any

from .latest_prices_adapter import query_latest_prices
from .query_intent import QueryRoute, recognize_query_intent
from .resolve_dataset_fields import LATEST_PRICE_FIELDS, resolve_dataset_fields
from .search_datasets import search_dataset_documents
from .search_instruments import _run_traced, search_instrument_documents


LATEST_PRICE_DOWNSTREAM_STAGES = (
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
    "latest_prices_query",
)
LATEST_PRICES_CATALOG_CONTEXT = "latest spot price quote"


def _route_guard_result(
    recognized_route: QueryRoute,
    requested_route: QueryRoute,
) -> dict[str, Any]:
    """判断识别出的路线是否等于当前请求路线且已经实现。"""

    if requested_route != QueryRoute.LATEST_PRICES:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "当前后端只接入 latest_prices，其他路线暂未实现",
        }
    if recognized_route != requested_route:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "用户问题属于其他业务路线，已停止 latest_prices 查询",
        }
    return {
        "accepted": True,
        "requested_route": requested_route.value,
        "recognized_route": recognized_route.value,
        "reason": "意图与当前 latest_prices 路线一致，可以继续",
    }


def _stopped_result(
    query: str,
    route: QueryRoute,
    query_intent: dict[str, Any],
    route_guard: dict[str, Any],
) -> dict[str, Any]:
    """为路线不匹配场景构造统一结果，方便前端显示停止原因。"""

    return {
        "query": query,
        "route": route.value,
        "query_intent": query_intent,
        "route_guard": route_guard,
        "methods": {},
        "warnings": [route_guard["reason"]],
        "master_resolution": {"resolved": 0, "inactive": 0, "not_found": 0},
        "candidates": [],
        "model_selection": None,
        "identifier_resolution": None,
        "dataset_search": None,
        "dataset_resolution": None,
        "field_resolution": None,
        "price_result": None,
    }


def _skipped_trace(
    trace_callback: Any,
    stage: str,
    stage_input: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """记录因前置阶段未通过而跳过的阶段，避免前端把它误认为尚未执行。"""

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


def search_latest_price_route(
    cursor: Any,
    query: str,
    limit: int = 3,
    use_embedding: bool = True,
    use_candidate_llm: bool = True,
    provider: str | None = None,
    requested_route: QueryRoute = QueryRoute.LATEST_PRICES,
    trace_callback: Any = None,
    query_intent_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行 latest_prices 路线直到返回最新价格。"""

    if not isinstance(requested_route, QueryRoute):
        requested_route = QueryRoute(requested_route)

    # 统一入口已经完成查询解析时直接复用结果，避免一次请求重复调用对话模型。
    # 直接调用本路线时 override 为空，仍然执行原有的查询解析流程。
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
        lambda: _route_guard_result(recognized_route, requested_route),
        lambda value: value,
    )
    if not route_guard["accepted"]:
        return _stopped_result(query, requested_route, query_intent, route_guard)

    instrument_query = query_intent.get("instrument_text")
    instrument_search_query = query_intent.get("instrument_search_text") or instrument_query
    if not instrument_query or not instrument_search_query:
        reason = "查询解析没有提取到金融工具主体，已停止工具检索"
        for stage in LATEST_PRICE_DOWNSTREAM_STAGES:
            _skipped_trace(trace_callback, stage, {}, reason)
        stopped = _stopped_result(query, requested_route, query_intent, route_guard)
        stopped["warnings"] = [reason]
        stopped["instrument_query"] = None
        stopped["instrument_search_query"] = None
        stopped["dataset_query"] = None
        return stopped

    # UI 参数优先；如果 UI 没有填写，则使用模型从原文摘录出的供应商文本。
    query_provider = provider or query_intent.get("provider_text")

    # instrument_query 保留用户原文；instrument_search_query 只在多语言场景提供
    # 英文召回提示。二者都不能直接作为正式 instrument_id，最终仍由 source 表确认。
    result = search_instrument_documents(
        cursor,
        instrument_search_query,
        limit=limit,
        use_embedding=use_embedding,
        use_candidate_llm=use_candidate_llm,
        provider=query_provider,
        trace_callback=trace_callback,
    )
    # 只有 instrument_identifier 已经在当前日期解析出唯一有效标识时，才允许
    # 进入数据集检索。这样 dataset_catalog.provider 可以和该标识的 provider 对齐，
    # 不会在供应商仍然不明确时盲目选择数据集。
    identifier_resolution = result.get("identifier_resolution")
    if identifier_resolution and identifier_resolution.get("status") == "resolved":
        selected_identifier = identifier_resolution.get("selected") or {}
        dataset_provider = selected_identifier.get("provider") or query_provider
        request_text = query_intent.get("request_text") or "latest spot price"
        dataset_query = f"{request_text} {LATEST_PRICES_CATALOG_CONTEXT}".strip()
        result["dataset_search"] = search_dataset_documents(
            cursor,
            dataset_query,
            limit=limit,
            use_embedding=use_embedding,
            use_candidate_llm=use_candidate_llm,
            # query_provider 只作为目录过滤条件；前序标识供应商仍作为独立的
            # expected_provider 一致性校验值，避免把两层职责混在一起。
            provider=query_provider,
            expected_provider=dataset_provider,
            trace_callback=trace_callback,
        )
        result["dataset_resolution"] = result["dataset_search"].get("dataset_resolution")
    else:
        # 前序没有得到唯一有效供应商标识时不执行数据集检索；保留可解释对象，
        # 前端可以明确看到“为什么没有进入 dataset_catalog”。
        result["dataset_search"] = {
            "status": "skipped",
            "provider_requested": query_provider,
            "warnings": ["没有唯一有效的 instrument_identifier，已跳过数据集目录检索"],
            "methods": {},
            "catalog_resolution": {},
            "candidates": [],
            "model_selection": None,
            "dataset_resolution": {
                "status": "skipped",
                "dataset_id": None,
                "storage_table_name": None,
                "reason": "instrument_identifier 未 resolved",
            },
        }
        result["dataset_resolution"] = result["dataset_search"]["dataset_resolution"]

    # dataset_id 确认后，字段目录只做同一数据集范围内的确定性读取；不再次调用
    # 大模型。latest_prices 的默认字段集合由路线规则固定，随后检查它们是否既在
    # dataset_field_catalog 中登记，又真实存在于 source.latest_prices。
    dataset_resolution = result.get("dataset_resolution")
    if dataset_resolution and dataset_resolution.get("status") == "resolved":
        result["field_resolution"] = _run_traced(
            trace_callback,
            "dataset_field_catalog",
            {
                "dataset_id": dataset_resolution.get("dataset_id"),
                "storage_table_name": dataset_resolution.get("storage_table_name"),
                "requested_fields": list(LATEST_PRICE_FIELDS),
                "selection_mode": "latest_prices_route_policy",
                "llm": False,
            },
            lambda: resolve_dataset_fields(
                cursor,
                dataset_resolution["dataset_id"],
                dataset_resolution["storage_table_name"],
                list(LATEST_PRICE_FIELDS),
            ),
            lambda value: value,
        )
        if result["field_resolution"].get("status") == "resolved":
            selected_identifier = identifier_resolution.get("selected") or {}
            selected_tool = result.get("model_selection") or {}
            result["price_result"] = _run_traced(
                trace_callback,
                "latest_prices_query",
                {
                    "instrument_id": selected_tool.get("instrument_id"),
                    "provider": selected_identifier.get("provider"),
                    "identifier": selected_identifier.get("identifier"),
                    "dataset_id": dataset_resolution.get("dataset_id"),
                    "storage_table_name": dataset_resolution.get("storage_table_name"),
                    "fields": [
                        field.get("physical_column_name")
                        for field in result["field_resolution"].get("fields", [])
                    ],
                    "order_by": "price_time DESC",
                    "limit": 1,
                },
                lambda: query_latest_prices(
                    cursor,
                    selected_tool["instrument_id"],
                    selected_identifier["provider"],
                    selected_identifier["identifier"],
                    dataset_resolution,
                    result["field_resolution"],
                    limit=1,
                ),
                lambda value: value,
            )
        else:
            result["price_result"] = _skipped_trace(
                trace_callback,
                "latest_prices_query",
                {
                    "dataset_id": dataset_resolution.get("dataset_id"),
                    "storage_table_name": dataset_resolution.get("storage_table_name"),
                },
                "字段目录或物理列校验未通过，已停止业务表查询",
            )
    else:
        result["field_resolution"] = _skipped_trace(
            trace_callback,
            "dataset_field_catalog",
            {"requested_fields": list(LATEST_PRICE_FIELDS)},
            "数据集目录未 resolved，已跳过字段目录查询",
        )
        result["price_result"] = _skipped_trace(
            trace_callback,
            "latest_prices_query",
            {"table": "source.latest_prices"},
            "字段计划未 resolved，已跳过业务表查询",
        )

    # search_instrument_documents 的内部 query 现在是 instrument_text；顶层结果仍
    # 保留用户原始问题，另存两个中间文本，便于前端和审计同时查看输入分流结果。
    result["query"] = query
    result["instrument_query"] = instrument_query
    result["instrument_search_query"] = instrument_search_query
    result["dataset_query"] = (
        result.get("dataset_search", {}).get("query")
        if isinstance(result.get("dataset_search"), dict)
        else None
    )
    result["route"] = requested_route.value
    result["query_intent"] = query_intent
    result["route_guard"] = route_guard
    return result
