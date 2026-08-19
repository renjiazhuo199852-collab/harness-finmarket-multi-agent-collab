"""``news_articles`` 独立新闻查询路线的在线编排。

新闻路线和三条行情/宏观路线保持独立：它不要求新闻表存在 ``source_identifier``，
也不把 ``EUR/USD`` 猜测成某条行情标识。用户主体经过查询解析后，直接作为新闻
文本/语义检索输入；数据集和字段目录仍然负责确认最终业务表与允许返回的字段。
"""

from __future__ import annotations

import os
from typing import Any

from .news_articles_adapter import NEWS_FIELDS, query_news_articles
from .query_intent import QueryRoute, recognize_query_intent
from .resolve_dataset_fields import resolve_dataset_fields
from .search_datasets import search_dataset_documents
from .search_instruments import _run_traced
from .search_news import search_news_documents


# 新闻链路的阶段顺序同时用于后端结果和前端滚动追踪面板。
NEWS_DOWNSTREAM_STAGES = (
    "dataset_exact_match",
    "dataset_keyword_search",
    "dataset_pg_trgm_search",
    "dataset_embedding_search",
    "dataset_rrf_merge",
    "dataset_catalog",
    "dataset_candidate_selector",
    "dataset_field_catalog",
    "news_exact_match",
    "news_keyword_search",
    "news_pg_trgm_search",
    "news_embedding_search",
    "news_rrf_merge",
    "news_articles_query",
)
# 数据集目录检索使用已经登记在英文目录中的稳定业务词。不能把中文
# ``相关新闻`` 与一串英文词用 AND 拼接，否则 PostgreSQL 全文检索会因为中文词
# 不在目录中而返回零候选；新闻主体本身仍然只进入后面的新闻文本/语义检索。
NEWS_DATASET_CONTEXT = "financial news"
NEWS_DATASET_ID = "LSEG_NEWS"
# 新闻表没有稳定的金融工具外键，检索输入必须同时保留用户主体和新闻业务上下文。
# 这个受控英文短语只用于提高新闻文档的语义召回，不会被当成表名、字段名或过滤条件。
NEWS_SEARCH_CONTEXT = "financial news"


def _news_route_guard(
    recognized_route: QueryRoute,
    requested_route: QueryRoute,
) -> dict[str, Any]:
    """限制当前请求只能执行 news_articles 路线。"""

    if requested_route != QueryRoute.NEWS_ARTICLES:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "当前后端请求不是 news_articles 路线",
        }
    if recognized_route != requested_route:
        return {
            "accepted": False,
            "requested_route": requested_route.value,
            "recognized_route": recognized_route.value,
            "reason": "用户问题属于其他业务路线，已停止 news_articles 查询",
        }
    return {
        "accepted": True,
        "requested_route": requested_route.value,
        "recognized_route": recognized_route.value,
        "reason": "意图与当前 news_articles 路线一致，可以继续",
    }


def _skip_stage(
    trace_callback: Any,
    stage: str,
    stage_input: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """记录前置条件不满足而跳过的阶段。"""

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


def _skip_stages(trace_callback: Any, reason: str) -> None:
    """补齐未执行阶段，前端可以明确看到链路停止位置。"""

    for stage in NEWS_DOWNSTREAM_STAGES:
        _skip_stage(trace_callback, stage, {}, reason)


def _stopped_result(
    query: str,
    requested_route: QueryRoute,
    query_intent: dict[str, Any],
    route_guard: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """构造统一的新闻路线停止结果。"""

    return {
        "query": query,
        "route": requested_route.value,
        "query_intent": query_intent,
        "route_guard": route_guard,
        "instrument_query": query_intent.get("instrument_text"),
        "news_query": None,
        "dataset_query": None,
        "warnings": [reason],
        "dataset_search": None,
        "dataset_resolution": None,
        "field_resolution": None,
        "news_search": None,
        "news_result": {"status": "skipped", "rows": [], "reason": reason},
    }


def search_news_articles_route(
    cursor: Any,
    query: str,
    limit: int = 3,
    use_embedding: bool = True,
    use_candidate_llm: bool = True,
    provider: str | None = None,
    start_date_override: Any | None = None,
    end_date_override: Any | None = None,
    requested_route: QueryRoute = QueryRoute.NEWS_ARTICLES,
    trace_callback: Any = None,
    query_intent_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行新闻路线，最终返回全部文本或语义相关的新闻候选。

    ``limit`` 仅用于数据集目录阶段的少量候选筛选；新闻事实候选不使用这个
    参数截断，确保最终结果不会因为目录候选上限而只返回固定几篇文章。
    """

    if not isinstance(requested_route, QueryRoute):
        requested_route = QueryRoute(requested_route)

    # 新闻统一入口同样复用前置的意图识别结果，避免重复消耗对话模型调用。
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
                "instrument_search_text",
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
        lambda: _news_route_guard(recognized_route, requested_route),
        lambda value: value,
    )
    if not route_guard["accepted"]:
        _skip_stages(trace_callback, route_guard["reason"])
        return _stopped_result(
            query,
            requested_route,
            query_intent,
            route_guard,
            route_guard["reason"],
        )

    instrument_query = query_intent.get("instrument_text")
    instrument_search_query = query_intent.get("instrument_search_text") or instrument_query
    # 新闻本身允许没有明确金融工具主体，例如“最新央行新闻”；这种情况下使用
    # 模型提取的 request_text 作为语义检索输入。EUR/USD 查询通常会优先走前两个值。
    news_subject = instrument_search_query or instrument_query or query_intent.get("request_text")
    news_query = " ".join(
        part for part in (news_subject, NEWS_SEARCH_CONTEXT) if part
    ).strip()
    if not news_query:
        reason = "查询解析没有提取到新闻主体或业务请求，已停止新闻检索"
        _skip_stages(trace_callback, reason)
        return _stopped_result(query, requested_route, query_intent, route_guard, reason)

    query_provider = provider or query_intent.get("provider_text")
    dataset_query = NEWS_DATASET_CONTEXT
    dataset_search = _run_traced(
        trace_callback,
        "news_dataset_route",
        {
            "query": dataset_query,
            "limit": limit,
            "allowed_dataset_ids": [NEWS_DATASET_ID],
            "provider": query_provider,
        },
        lambda: search_dataset_documents(
            cursor,
            dataset_query,
            limit=limit,
            use_embedding=use_embedding,
            use_candidate_llm=use_candidate_llm,
            provider=query_provider,
            expected_provider=query_provider,
            allowed_dataset_ids={NEWS_DATASET_ID},
            trace_callback=trace_callback,
        ),
        lambda value: value,
    )
    dataset_resolution = dataset_search.get("dataset_resolution")
    warnings = list(dataset_search.get("warnings") or [])

    if not isinstance(dataset_resolution, dict) or dataset_resolution.get("status") != "resolved":
        reason = "LSEG_NEWS 数据集未 resolved，已跳过字段目录和新闻事实查询"
        _skip_stage(trace_callback, "dataset_field_catalog", {}, reason)
        _skip_stage(trace_callback, "news_articles_query", {}, reason)
        return {
            "query": query,
            "route": requested_route.value,
            "query_intent": query_intent,
            "route_guard": route_guard,
            "instrument_query": instrument_query,
            "instrument_search_query": instrument_search_query,
            "news_query": news_query,
            "dataset_query": dataset_query,
            "warnings": [*warnings, reason],
            "dataset_search": dataset_search,
            "dataset_resolution": dataset_resolution,
            "field_resolution": {
                "status": "skipped",
                "dataset_id": dataset_resolution.get("dataset_id"),
                "storage_table_name": dataset_resolution.get("storage_table_name"),
                "requested_fields": list(NEWS_FIELDS),
                "fields": [],
                "reason": reason,
            },
            "news_search": None,
            "news_result": {"status": "skipped", "rows": [], "reason": reason},
        }

    field_resolution = _run_traced(
        trace_callback,
        "dataset_field_catalog",
        {
            "dataset_id": dataset_resolution.get("dataset_id"),
            "storage_table_name": dataset_resolution.get("storage_table_name"),
            "requested_fields": list(NEWS_FIELDS),
            "selection_mode": "news_text_candidate_route_policy",
            "llm": False,
        },
        lambda: resolve_dataset_fields(
            cursor,
            dataset_resolution["dataset_id"],
            dataset_resolution["storage_table_name"],
            list(NEWS_FIELDS),
        ),
        lambda value: value,
    )
    if field_resolution.get("status") != "resolved":
        reason = "新闻字段目录或 source.news_articles 物理列校验未通过"
        _skip_stage(trace_callback, "news_articles_query", {}, reason)
        return {
            "query": query,
            "route": requested_route.value,
            "query_intent": query_intent,
            "route_guard": route_guard,
            "instrument_query": instrument_query,
            "instrument_search_query": instrument_search_query,
            "news_query": news_query,
            "dataset_query": dataset_query,
            "warnings": [*warnings, reason],
            "dataset_search": dataset_search,
            "dataset_resolution": dataset_resolution,
            "field_resolution": field_resolution,
            "news_search": None,
            "news_result": {"status": "skipped", "rows": [], "reason": reason},
        }

    news_search = search_news_documents(
        cursor,
        news_query,
        # 新闻路线要求保留所有相关候选；None 会让四路召回、RRF 和源表回查都不
        # 添加最终条数限制。数据量控制由查询主体、供应商和日期条件负责。
        limit=None,
        use_embedding=use_embedding,
        provider=query_provider,
        start_date=start_date_override,
        end_date=end_date_override,
        trace_callback=trace_callback,
    )
    news_result = _run_traced(
        trace_callback,
        "news_articles_query",
        {
            "dataset_id": dataset_resolution.get("dataset_id"),
            "storage_table_name": dataset_resolution.get("storage_table_name"),
            "source_table": "source.news_articles",
            "candidate_count": len(news_search.get("candidates") or []),
            "fields": [field.get("physical_column_name") for field in field_resolution.get("fields", [])],
            "start_date": start_date_override,
            "end_date": end_date_override,
            "return_shape": {"data": list(NEWS_FIELDS), "metadata": ["article_id", "source", "publish_time"]},
        },
        lambda: query_news_articles(
            cursor,
            news_search.get("candidates") or [],
            dataset_resolution,
            field_resolution,
            limit=None,
            start_date=start_date_override,
            end_date=end_date_override,
        ),
        lambda value: _news_result_for_trace(value),
    )
    warnings.extend(news_search.get("warnings") or [])
    return {
        "query": query,
        "route": requested_route.value,
        "query_intent": query_intent,
        "route_guard": route_guard,
        "instrument_query": instrument_query,
        "instrument_search_query": instrument_search_query,
        "news_query": news_query,
        "dataset_query": dataset_query,
        "warnings": warnings,
        "dataset_search": dataset_search,
        "dataset_resolution": dataset_resolution,
        "field_resolution": field_resolution,
        "news_search": news_search,
        "news_result": news_result,
    }


def _news_result_for_trace(value: dict[str, Any]) -> dict[str, Any]:
    """为 SSE 只保留新闻结果摘要，避免正文内容把调试面板撑爆。"""

    rows = []
    for row in value.get("rows") or []:
        data = row.get("data") or {}
        metadata = row.get("metadata") or {}
        content = data.get("content") or ""
        rows.append(
            {
                "article_id": metadata.get("article_id"),
                "source": metadata.get("source"),
                "publish_time": metadata.get("publish_time"),
                "title": data.get("title"),
                "summary": data.get("summary"),
                "content_preview": str(content)[:300],
                "rrf_score": metadata.get("rrf_score"),
            }
        )
    return {
        "status": value.get("status"),
        "dataset_id": value.get("dataset_id"),
        "storage_table_name": value.get("storage_table_name"),
        "fields": value.get("fields"),
        "row_count": value.get("row_count"),
        "rows": rows,
        "reason": value.get("reason"),
    }
