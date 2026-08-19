"""``macro_observations`` 独立查询路线的在线编排。

本路线复用共享的查询解析、金融工具混合检索和数据集目录检索，但业务边界独立：

* 用户主体必须先解析成宏观指标、政策利率或债券收益率工具；
* 工具和供应商标识必须经过 ``source`` 主数据表确认；
* 当前只执行字段目录已经登记的 ``LSEG_MACRO``；
* 利率和债券收益率会走到数据集确认，但因字段目录尚未登记而安全停止。

适配器只访问 ``source.macro_observations``，不让大模型生成表名、列名或 SQL。
"""

from __future__ import annotations

from datetime import date, timedelta
import os
from typing import Any

from .macro_observation_request import (
    MACRO_DEFAULT_ROW_LIMIT,
    MACRO_FIELDS,
    parse_macro_observation_request,
)
from .macro_observations_adapter import (
    MACRO_DATASET_ID,
    query_macro_observations,
)
from .query_intent import QueryRoute, recognize_query_intent
from .resolve_dataset_fields import resolve_dataset_fields
from .search_datasets import search_dataset_documents
from .search_instruments import _run_traced, search_instrument_documents


MACRO_INSTRUMENT_TYPES = {"METRIC", "INTEREST_RATE", "BOND_YIELD"}
MACRO_DATASET_IDS_BY_TYPE = {
    "METRIC": {"LSEG_MACRO"},
    "INTEREST_RATE": {"LSEG_INTEREST_RATE"},
    "BOND_YIELD": {"LSEG_BOND_YIELD"},
}
MACRO_CATALOG_CONTEXT_BY_TYPE = {
    "METRIC": "macro economic indicator CPI GDP PMI actual previous forecast revised",
    "INTEREST_RATE": "central bank policy interest rate",
    "BOND_YIELD": "government bond yield treasury yield",
}

MACRO_DOWNSTREAM_STAGES = (
    "macro_observation_request",
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
    "macro_observations_query",
)


def _route_guard_result(
    recognized_route: QueryRoute,
    requested_route: QueryRoute,
) -> dict[str, Any]:
    """确认当前请求只能执行 ``macro_observations`` 路线。"""

    if requested_route != QueryRoute.MACRO_OBSERVATIONS:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "当前后端请求不是 macro_observations 路线",
        }
    if recognized_route != requested_route:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "用户问题属于其他业务路线，已停止 macro_observations 查询",
        }
    return {
        "accepted": True,
        "requested_route": requested_route.value,
        "recognized_route": recognized_route.value,
        "reason": "意图与当前 macro_observations 路线一致，可以继续",
    }


def _skip_stage(
    trace_callback: Any,
    stage: str,
    stage_input: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """记录因前置条件未满足而跳过的阶段。"""

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
    """批量补齐未执行阶段的 skipped 事件，方便前端完整展示路线。"""

    for stage in stages:
        _skip_stage(trace_callback, stage, {}, reason)


def _stopped_result(
    query: str,
    requested_route: QueryRoute,
    query_intent: dict[str, Any],
    route_guard: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """构造路线停止时的统一返回对象。"""

    return {
        "query": query,
        "route": requested_route.value,
        "query_intent": query_intent,
        "route_guard": route_guard,
        "macro_observation_request": None,
        "methods": {},
        "warnings": [reason],
        "master_resolution": {"resolved": 0, "inactive": 0, "not_found": 0},
        "candidates": [],
        "model_selection": None,
        "identifier_resolution": None,
        "dataset_search": None,
        "dataset_resolution": None,
        "field_resolution": None,
        "macro_observations_result": {
            "status": "skipped",
            "rows": [],
            "reason": reason,
        },
        "price_result": None,
        "market_bars_result": None,
    }


def _identifier_as_of_date(request: dict[str, Any]) -> date:
    """把历史查询的结束边界转换为标识有效期校验日期。"""

    end_date = request.get("end_date")
    if end_date:
        # end_date 是左闭右开边界，因此最后一个有效查询日是前一天。
        return date.fromisoformat(end_date) - timedelta(days=1)
    return date.today()


def _unsupported_field_result(
    dataset_resolution: dict[str, Any],
    reason: str,
    requested_fields: list[str],
) -> dict[str, Any]:
    """构造未登记逻辑数据集的字段阶段结果。"""

    return {
        "status": "unsupported_dataset",
        "dataset_id": dataset_resolution.get("dataset_id"),
        "storage_table_name": dataset_resolution.get("storage_table_name"),
        "requested_fields": requested_fields,
        "fields": [],
        "available_fields": [],
        "missing_catalog_fields": requested_fields,
        "missing_physical_columns": [],
        "reason": reason,
    }


def search_macro_observations_route(
    cursor: Any,
    query: str,
    limit: int = 3,
    row_limit: int = MACRO_DEFAULT_ROW_LIMIT,
    use_embedding: bool = True,
    use_candidate_llm: bool = True,
    provider: str | None = None,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    requested_route: QueryRoute = QueryRoute.MACRO_OBSERVATIONS,
    trace_callback: Any = None,
    query_intent_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行宏观指标查询，当前完整支持 ``METRIC -> LSEG_MACRO``。"""

    if not isinstance(requested_route, QueryRoute):
        requested_route = QueryRoute(requested_route)

    # 统一入口已经完成意图识别时复用结构化结果，避免宏观路线再次请求模型。
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
        reason = route_guard["reason"]
        _skip_stages(trace_callback, MACRO_DOWNSTREAM_STAGES, reason)
        return _stopped_result(query, requested_route, query_intent, route_guard, reason)

    macro_request = _run_traced(
        trace_callback,
        "macro_observation_request",
        {
            "time_expression": query_intent.get("time_expression"),
            "request_text": query_intent.get("request_text"),
            "start_date_override": start_date_override.isoformat()
            if start_date_override
            else None,
            "end_date_override": end_date_override.isoformat() if end_date_override else None,
            "row_limit": row_limit,
        },
        lambda: parse_macro_observation_request(
            query_intent.get("time_expression"),
            query_intent.get("request_text"),
            start_date_override=start_date_override,
            end_date_override=end_date_override,
            row_limit=row_limit,
        ),
        lambda value: value,
    )
    if macro_request.get("status") != "resolved":
        reason = str(macro_request.get("reason") or "宏观查询参数未通过校验")
        _skip_stages(trace_callback, MACRO_DOWNSTREAM_STAGES[1:], reason)
        return {
            **_stopped_result(query, requested_route, query_intent, route_guard, reason),
            "macro_observation_request": macro_request,
        }

    instrument_query = query_intent.get("instrument_text")
    instrument_search_query = query_intent.get("instrument_search_text") or instrument_query
    if not instrument_query or not instrument_search_query:
        reason = "查询解析没有提取到宏观指标主体，已停止工具检索"
        _skip_stages(trace_callback, MACRO_DOWNSTREAM_STAGES[2:], reason)
        stopped = {
            **_stopped_result(query, requested_route, query_intent, route_guard, reason),
            "macro_observation_request": macro_request,
            "instrument_query": None,
            "instrument_search_query": None,
            "dataset_query": None,
        }
        return stopped

    query_provider = provider or query_intent.get("provider_text")
    result = search_instrument_documents(
        cursor,
        instrument_search_query,
        limit=limit,
        use_embedding=use_embedding,
        use_candidate_llm=use_candidate_llm,
        provider=query_provider,
        identifier_as_of_date=_identifier_as_of_date(macro_request),
        allowed_instrument_types=MACRO_INSTRUMENT_TYPES,
        trace_callback=trace_callback,
    )
    warnings = list(result.get("warnings") or [])
    identifier_resolution = result.get("identifier_resolution")
    selected_tool = result.get("model_selection") or {}
    selected_candidate = selected_tool.get("candidate") or {}
    instrument_type = str(selected_candidate.get("instrument_type") or "").upper()

    if identifier_resolution and identifier_resolution.get("status") == "resolved":
        selected_identifier = identifier_resolution.get("selected") or {}
        dataset_provider = selected_identifier.get("provider") or query_provider
        dataset_ids = MACRO_DATASET_IDS_BY_TYPE.get(instrument_type, set())
        catalog_context = MACRO_CATALOG_CONTEXT_BY_TYPE.get(
            instrument_type,
            "macro observations",
        )
        request_text = query_intent.get("request_text") or "latest macro observation"
        dataset_query = " ".join(
            part
            for part in (
                request_text,
                catalog_context,
                selected_candidate.get("master_name"),
                selected_candidate.get("master_description"),
            )
            if part
        ).strip()
        result["dataset_search"] = search_dataset_documents(
            cursor,
            dataset_query,
            limit=limit,
            use_embedding=use_embedding,
            use_candidate_llm=use_candidate_llm,
            provider=query_provider,
            expected_provider=dataset_provider,
            allowed_dataset_ids=dataset_ids,
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
                "reason": reason,
            },
        }
        result["dataset_resolution"] = result["dataset_search"]["dataset_resolution"]

    dataset_resolution = result.get("dataset_resolution") or {}
    if dataset_resolution.get("status") != "resolved":
        reason = "数据集目录未 resolved，已跳过字段目录和 macro_observations 查询"
        _skip_stage(trace_callback, "dataset_field_catalog", {}, reason)
        _skip_stage(trace_callback, "macro_observations_query", {}, reason)
        result["field_resolution"] = _unsupported_field_result(
            dataset_resolution,
            reason,
            list(MACRO_FIELDS),
        )
        result["macro_observations_result"] = {
            "status": "skipped",
            "rows": [],
            "reason": reason,
        }
    elif dataset_resolution.get("dataset_id") != MACRO_DATASET_ID:
        reason = (
            f"当前暂不处理 {dataset_resolution.get('dataset_id')}："
            "该逻辑数据集尚未登记对应的 dataset_field_catalog 字段"
        )
        warnings.append(reason)
        _skip_stage(trace_callback, "dataset_field_catalog", {}, reason)
        _skip_stage(trace_callback, "macro_observations_query", {}, reason)
        result["field_resolution"] = _unsupported_field_result(
            dataset_resolution,
            reason,
            list(MACRO_FIELDS),
        )
        result["macro_observations_result"] = {
            "status": "unsupported_dataset",
            "rows": [],
            "dataset_id": dataset_resolution.get("dataset_id"),
            "storage_table_name": dataset_resolution.get("storage_table_name"),
            "reason": reason,
        }
    else:
        result["field_resolution"] = _run_traced(
            trace_callback,
            "dataset_field_catalog",
            {
                "dataset_id": dataset_resolution.get("dataset_id"),
                "storage_table_name": dataset_resolution.get("storage_table_name"),
                "requested_fields": list(MACRO_FIELDS),
                "selection_mode": "macro_metric_route_policy",
                "llm": False,
            },
            lambda: resolve_dataset_fields(
                cursor,
                dataset_resolution["dataset_id"],
                dataset_resolution["storage_table_name"],
                list(MACRO_FIELDS),
            ),
            lambda value: value,
        )
        if result["field_resolution"].get("status") == "resolved":
            selected_identifier = identifier_resolution.get("selected") or {}
            result["macro_observations_result"] = _run_traced(
                trace_callback,
                "macro_observations_query",
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
                    "start_date": macro_request.get("start_date"),
                    "end_date": macro_request.get("end_date"),
                    "frequency": macro_request.get("frequency"),
                    "order_by": "release_time DESC, id DESC",
                    "limit": macro_request.get("row_limit"),
                    "linked_rows_only": True,
                },
                lambda: query_macro_observations(
                    cursor,
                    selected_tool["instrument_id"],
                    selected_identifier["provider"],
                    selected_identifier["identifier"],
                    dataset_resolution,
                    result["field_resolution"],
                    start_date=(
                        date.fromisoformat(macro_request["start_date"])
                        if macro_request.get("start_date")
                        else None
                    ),
                    end_date=(
                        date.fromisoformat(macro_request["end_date"])
                        if macro_request.get("end_date")
                        else None
                    ),
                    frequency=macro_request.get("frequency"),
                    limit=macro_request["row_limit"],
                ),
                lambda value: value,
            )
        else:
            reason = "字段目录或物理列校验未通过，已停止 macro_observations 查询"
            _skip_stage(trace_callback, "macro_observations_query", {}, reason)
            result["macro_observations_result"] = {
                "status": "skipped",
                "rows": [],
                "reason": reason,
            }

    # 顶层结果保留完整用户问题，同时记录真正送入两个检索阶段的文本。
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
    result["macro_observation_request"] = macro_request
    result["price_result"] = None
    result["market_bars_result"] = None
    return result
