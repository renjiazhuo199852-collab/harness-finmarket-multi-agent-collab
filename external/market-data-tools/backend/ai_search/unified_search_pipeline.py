"""目录驱动的统一查询编排。

统一入口不再让查询解析模型输出固定的四条路线，而是把：

``用户问题 -> dataset_catalog 候选 -> 候选一致性校验 -> 正式数据集``

作为唯一的数据集发现过程。正式目录返回 ``storage_table_name`` 后，程序从受控
适配器注册表中找到业务查询处理器。表名、字段名、金融工具 ID 和供应商标识都
必须来自已经确认的 source 目录或主数据表，模型不能生成这些值。

现有四个独立接口可以把自己的页面范围作为兼容约束传入本模块，但统一入口本身
不接收 route，也不会让模型选择一个固定路线枚举。
"""

from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any, Callable

from .latest_prices_adapter import LATEST_PRICES_TABLE, query_latest_prices
from .instrument_master_adapter import (
    INSTRUMENT_MASTER_FIELDS,
    INSTRUMENT_MASTER_TABLE,
    query_instrument_master,
)
from .macro_observation_request import (
    MACRO_FIELDS,
    parse_macro_observation_request,
)
from .macro_observations_adapter import query_macro_observations
from .market_bar_request import parse_market_bar_request
from .market_bars_adapter import (
    MARKET_BAR_FIELDS,
    MARKET_BARS_FREQUENCY,
    MARKET_BARS_TABLE,
    query_market_bars,
)
from .news_articles_adapter import NEWS_FIELDS, NEWS_TABLE, query_news_articles
from .query_parser import parse_query_understanding
from .resolve_dataset_fields import resolve_dataset_fields
from .search_datasets import search_dataset_documents
from .search_instruments import _run_traced, search_instrument_documents
from .search_news import search_news_documents


TraceCallback = Callable[[dict[str, Any]], None]


# 适配器以 source.dataset_catalog 返回的物理表名为索引，而不是以模型生成的
# 意图字符串为索引。新增数据集时需要登记对应处理器；统一查询理解模块不需要改动。
ADAPTER_REGISTRY: dict[str, dict[str, Any]] = {
    LATEST_PRICES_TABLE: {
        "adapter": "latest_prices",
        "fields": tuple(
            ("price_time", "last", "bid", "ask", "mid")
        ),
        "requires_instrument_identity": True,
        "allowed_instrument_types": None,
    },
    MARKET_BARS_TABLE: {
        "adapter": "market_bars",
        "fields": MARKET_BAR_FIELDS,
        "requires_instrument_identity": True,
        "allowed_instrument_types": None,
    },
    "macro_observations": {
        "adapter": "macro_observations",
        "fields": MACRO_FIELDS,
        "requires_instrument_identity": True,
        "allowed_instrument_types": {"METRIC", "INTEREST_RATE", "BOND_YIELD"},
    },
    NEWS_TABLE: {
        "adapter": "news_articles",
        "fields": NEWS_FIELDS,
        "requires_instrument_identity": False,
        "allowed_instrument_types": None,
    },
    INSTRUMENT_MASTER_TABLE: {
        "adapter": "instrument_master",
        "fields": INSTRUMENT_MASTER_FIELDS,
        "requires_instrument_identity": False,
        "resolves_instrument_master": True,
        "allowed_instrument_types": None,
    },
}


def _skip_stage(
    trace_callback: TraceCallback | None,
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


def _rejected_execution(code: str, reason: str) -> dict[str, Any]:
    """构造统一的业务执行停止结果，不返回任何业务事实行。"""

    return {
        "status": "rejected",
        "code": code,
        "adapter": None,
        "rows": [],
        "row_count": 0,
        "reason": reason,
    }


def _date_from_parts(year: str, month: str, day: str) -> date:
    """把自然语言日期片段转换为日期对象，并统一处理非法日期异常。"""

    return date(int(year), int(month), int(day))


def parse_news_date_range(
    expression: str | None,
    *,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """解析新闻路线日期，并把结束日期转换成左闭右开边界。

    ``source.news_articles.publish_time`` 包含时分秒，因此结束日期统一使用下一天
    的零点。前端日期控件输入的结束日也按“包含当天”处理，避免用户选择 8 月 10 日
    后漏掉当天晚些时候发布的文章。
    """

    expression = (expression or "").strip()
    today = reference_date or date.today()
    start = start_date_override
    end = end_date_override + timedelta(days=1) if end_date_override else None

    if start is None and end is None and expression:
        dates: list[date] = []
        pattern = re.compile(
            r"(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*(\d{1,2})\s*日?"
        )
        for match in pattern.finditer(expression):
            dates.append(_date_from_parts(*match.groups()))
        if len(dates) >= 2:
            start, end = dates[0], dates[1] + timedelta(days=1)
        elif len(dates) == 1:
            start, end = dates[0], dates[0] + timedelta(days=1)
        elif re.search(r"最近\s*(?:一周|7天)|过去\s*7天|last\s+week", expression, re.I):
            start, end = today - timedelta(days=7), today + timedelta(days=1)
        elif re.search(r"最近\s*(?:一个月|30天)|过去\s*30天|last\s+month", expression, re.I):
            start, end = today - timedelta(days=30), today + timedelta(days=1)
        elif re.search(r"本月|this\s+month", expression, re.I):
            start = today.replace(day=1)
            end = today + timedelta(days=1)

    if start is not None and end is not None and start >= end:
        return {
            "status": "invalid",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "reason": "新闻查询开始日期必须早于结束日期",
        }
    return {
        "status": "resolved",
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "reason": "已解析新闻发布时间范围" if start else "未指定新闻日期范围",
    }


def _remove_dataset_filter_terms(
    value: str,
    understanding: dict[str, Any],
) -> str:
    """从数据集召回文本中移除日期和供应商过滤词。

    查询理解模型会同时输出召回改写和独立过滤条件。模型通常会遵守“日期单独
    返回”的约束，但不能把数据库检索安全性建立在模型永远不犯这种重复上的假设
    上，因此这里再做一层程序过滤。该过滤只作用于数据集候选文本，不会修改原始
    问题、查询理解结果或后续业务适配器的日期条件。
    """

    clean_value = value
    for field_name in ("time_expression", "provider_text"):
        field_value = understanding.get(field_name)
        if field_value:
            clean_value = re.sub(
                re.escape(str(field_value)),
                " ",
                clean_value,
                flags=re.IGNORECASE,
            )

    # 同时覆盖模型可能生成的中英文日期短语；不删除 latest price 等业务请求词。
    temporal_patterns = (
        r"\b(?:last|past|recent|latest|this)\s+(?:day|week|month|quarter|year)\b",
        r"\b(?:today|yesterday|tomorrow)\b",
        r"\b20\d{2}(?:[-/.]\d{1,2}){1,2}\b",
        r"最近\s*(?:一日|一天|一周|一个月|一季度|一年|7天|30天|90天|365天)",
        r"过去\s*(?:一日|一天|一周|一个月|一季度|一年|7天|30天|90天|365天)",
        r"本(?:日|周|月|季度|年)",
        r"\d{1,3}\s*(?:天|周|个月|季度|年)内",
    )
    for pattern in temporal_patterns:
        clean_value = re.sub(pattern, " ", clean_value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", clean_value).strip()


def _query_context_text(understanding: dict[str, Any], original_query: str) -> str:
    """组合数据集召回文本，不人为追加固定业务后缀或过滤条件。"""

    parts = [
        understanding.get("subject_search_text"),
        understanding.get("request_text"),
        understanding.get("query_rewrite"),
        *(understanding.get("search_terms") or []),
    ]
    text = " ".join(str(part).strip() for part in parts if part and str(part).strip())
    filtered = _remove_dataset_filter_terms(text, understanding)
    return filtered or original_query


def _understanding_for_trace(value: dict[str, Any]) -> dict[str, Any]:
    """返回可展示的查询理解结果，不包含任何敏感配置。"""

    return value


def _resolve_provider(
    request_provider: str | None,
    understanding: dict[str, Any],
) -> str | None:
    """计算供应商过滤值；显式接口参数优先，未指定时保持空值。"""

    explicit = request_provider.strip() if request_provider else None
    parsed = understanding.get("provider_text")
    if explicit and parsed and explicit.casefold() != str(parsed).casefold():
        raise ValueError("请求参数 provider 与自然语言中的供应商不一致")
    return explicit or parsed


def _parse_adapter_request(
    storage_table_name: str,
    understanding: dict[str, Any],
    *,
    row_limit: int,
    start_date_override: date | None,
    end_date_override: date | None,
    trace_callback: TraceCallback | None,
) -> dict[str, Any] | None:
    """执行当前适配器的日期和频率解析。"""

    expression = " ".join(
        part
        for part in (
            understanding.get("time_expression"),
            understanding.get("request_text"),
        )
        if part
    ).strip()
    if storage_table_name == MARKET_BARS_TABLE:
        return _run_traced(
            trace_callback,
            "market_bar_request",
            {
                "query": expression,
                "start_date_override": start_date_override.isoformat()
                if start_date_override
                else None,
                "end_date_override": end_date_override.isoformat()
                if end_date_override
                else None,
                "row_limit": row_limit,
                "supported_frequency": MARKET_BARS_FREQUENCY,
            },
            lambda: parse_market_bar_request(
                expression,
                start_date_override=start_date_override,
                end_date_override=end_date_override,
                row_limit=row_limit,
            ),
            lambda value: value,
        )
    if storage_table_name == "macro_observations":
        return _run_traced(
            trace_callback,
            "macro_observation_request",
            {
                "time_expression": understanding.get("time_expression"),
                "request_text": understanding.get("request_text"),
                "start_date_override": start_date_override.isoformat()
                if start_date_override
                else None,
                "end_date_override": end_date_override.isoformat()
                if end_date_override
                else None,
                "row_limit": row_limit,
            },
            lambda: parse_macro_observation_request(
                understanding.get("time_expression"),
                understanding.get("request_text"),
                start_date_override=start_date_override,
                end_date_override=end_date_override,
                row_limit=row_limit,
            ),
            lambda value: value,
        )
    if storage_table_name == NEWS_TABLE:
        return _run_traced(
            trace_callback,
            "news_date_request",
            {
                "time_expression": understanding.get("time_expression"),
                "start_date_override": start_date_override.isoformat()
                if start_date_override
                else None,
                "end_date_override": end_date_override.isoformat()
                if end_date_override
                else None,
            },
            lambda: parse_news_date_range(
                understanding.get("time_expression"),
                start_date_override=start_date_override,
                end_date_override=end_date_override,
            ),
            lambda value: value,
        )
    return None


def _identifier_as_of_date(
    storage_table_name: str,
    adapter_request: dict[str, Any] | None,
) -> date:
    """按业务请求的结束边界选择供应商标识校验日期。"""

    if storage_table_name == MARKET_BARS_TABLE and adapter_request:
        end_text = adapter_request.get("end_date")
        if end_text:
            return date.fromisoformat(end_text)
    if storage_table_name == "macro_observations" and adapter_request:
        end_text = adapter_request.get("end_date")
        if end_text:
            # 宏观解析的 end_date 是左闭右开边界，标识校验使用实际查询的最后一天。
            return date.fromisoformat(end_text) - timedelta(days=1)
    return date.today()


def _attach_legacy_route_fields(
    result: dict[str, Any],
    compatibility_route: str,
) -> None:
    """为四个旧测试页面补齐兼容字段。

    统一入口的新协议以 ``dataset_search``、``dataset_resolution`` 和 ``execution``
    为核心。旧页面已经分别读取 ``candidates``、``model_selection``、
    ``price_result`` 等字段，因此这里仅做响应适配，不复制任何查询逻辑。这样旧
    页面仍可以继续测试，但实际数据集发现、字段读取和业务 SQL 仍只有一套实现。
    """

    instrument_search = result.get("instrument_search") or {}
    dataset_search = result.get("dataset_search") or {}
    understanding = result.get("query_understanding") or {}
    request_resolution = result.get("request_resolution")

    # 旧页面的候选区表示金融工具；数据集候选仍保留在 dataset_search.candidates。
    result["methods"] = instrument_search.get("methods") or dataset_search.get("methods", {})
    result["master_resolution"] = instrument_search.get("master_resolution", {})
    result["candidates"] = instrument_search.get("candidates", [])
    result["model_selection"] = instrument_search.get("model_selection")
    result["instrument_query"] = understanding.get("subject_text")
    result["instrument_search_query"] = understanding.get("subject_search_text")
    result["query_intent"] = {
        "route": compatibility_route,
        "confidence": understanding.get("confidence", 0),
        "reason": understanding.get("reason", ""),
        "instrument_text": understanding.get("subject_text"),
        "instrument_search_text": understanding.get("subject_search_text"),
        "provider_text": understanding.get("provider_text"),
        "time_expression": understanding.get("time_expression"),
        "request_text": understanding.get("request_text"),
    }

    if isinstance(request_resolution, dict):
        if compatibility_route == "market_bars":
            result["market_bar_request"] = request_resolution
        elif compatibility_route == "macro_observations":
            result["macro_observation_request"] = request_resolution

    execution = result.get("execution")
    if not isinstance(execution, dict):
        return
    # 业务结果字段是同一份 execution 对象的兼容别名，避免旧页面看到第二份数据。
    if compatibility_route == "latest_prices":
        result["price_result"] = execution
    elif compatibility_route == "market_bars":
        result["market_bars_result"] = execution
    elif compatibility_route == "macro_observations":
        result["macro_observations_result"] = execution
    elif compatibility_route == "news_articles":
        result["news_result"] = execution


def _remove_legacy_business_results(result: dict[str, Any]) -> None:
    """删除页面兼容别名，防止路线拒绝时误显示业务查询成功结果。"""

    for key in (
        "price_result",
        "market_bars_result",
        "macro_observations_result",
        "news_result",
    ):
        result.pop(key, None)


def _compatibility_result(
    result: dict[str, Any],
    *,
    compatibility_route: str | None,
    expected_storage_table_name: str | None,
) -> dict[str, Any]:
    """为旧独立接口补齐旧字段，不影响统一接口的新响应结构。"""

    if compatibility_route is None:
        return result

    _attach_legacy_route_fields(result, compatibility_route)
    selected_table = (result.get("dataset_resolution") or {}).get("storage_table_name")
    accepted = (
        expected_storage_table_name is None
        or (
            (result.get("dataset_resolution") or {}).get("status") == "resolved"
            and selected_table == expected_storage_table_name
        )
    )
    if not accepted:
        reason = "用户问题对应的数据集与当前独立页面范围不一致"
    elif result.get("status") == "success":
        reason = "数据集与独立页面范围一致"
    else:
        reason = "数据集与独立页面范围一致，但后续查询未完成"
    result["route"] = compatibility_route
    recognized_route = {
        "latest_prices": "latest_prices",
        "market_bars": "market_bars",
        "macro_observations": "macro_observations",
        "news_articles": "news_articles",
    }.get(str(selected_table), compatibility_route)
    result["route_guard"] = {
        "accepted": accepted,
        "requested_route": compatibility_route,
        "recognized_route": recognized_route,
        "reason": reason,
    }
    result["compatibility_route_guard"] = result["route_guard"]
    if not accepted:
        result["status"] = "rejected"
        result["execution"] = _rejected_execution("ROUTE_DATASET_MISMATCH", reason)
        result["adapter"] = None
        _remove_legacy_business_results(result)
        result["warnings"] = list(result.get("warnings") or []) + [reason]
    return result


def run_unified_query(
    cursor: Any,
    query: str,
    *,
    limit: int = 3,
    row_limit: int = 100,
    provider: str | None = None,
    use_embedding: bool = True,
    use_candidate_llm: bool = True,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    trace_callback: TraceCallback | None = None,
    query_understanding_override: dict[str, Any] | None = None,
    compatibility_route: str | None = None,
    expected_storage_table_name: str | None = None,
    allowed_dataset_ids: set[str] | None = None,
) -> dict[str, Any]:
    """执行一次目录驱动的统一查询。"""

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("查询文本不能为空")
    if limit < 1 or limit > 100:
        raise ValueError("数据集和工具候选 limit 必须在 1 到 100 之间")

    understanding = query_understanding_override or _run_traced(
        trace_callback,
        "query_understanding",
        {
            "query": clean_query,
            "output_fields": [
                "subject_text",
                "subject_search_text",
                "provider_text",
                "time_expression",
                "request_text",
                "query_rewrite",
                "search_terms",
            ],
        },
        lambda: parse_query_understanding(clean_query),
        _understanding_for_trace,
    )

    try:
        query_provider = _resolve_provider(provider, understanding)
    except ValueError as exc:
        # 请求参数和自然语言中的供应商属于同一条业务约束，冲突时直接返回结构化
        # 拒绝，不进入数据集检索，更不能让后续模块用任意一个值继续查询。
        reason = str(exc)
        result = {
            "status": "rejected",
            "query": clean_query,
            "query_understanding": understanding,
            "dataset_query": None,
            "dataset_search": None,
            "dataset_resolution": {
                "status": "rejected",
                "dataset_id": None,
                "storage_table_name": None,
                "provider": None,
                "reason": reason,
            },
            "dataset_consistency_check": {
                "status": "rejected",
                "code": "DATASET_PROVIDER_MISMATCH",
                "selected_dataset_id": None,
                "candidate_dataset_ids": [],
                "reason": reason,
            },
            "field_resolution": None,
            "instrument_resolution": None,
            "identifier_resolution": None,
            "execution": _rejected_execution("DATASET_PROVIDER_MISMATCH", reason),
            "warnings": [reason],
        }
        return _compatibility_result(
            result,
            compatibility_route=compatibility_route,
            expected_storage_table_name=expected_storage_table_name,
        )
    dataset_query = _query_context_text(understanding, clean_query)
    dataset_search = search_dataset_documents(
        cursor,
        dataset_query,
        limit=limit,
        use_embedding=use_embedding,
        use_candidate_llm=use_candidate_llm,
        provider=query_provider,
        candidate_selection_query=clean_query,
        candidate_selection_context=understanding,
        require_candidate_llm=True,
        allowed_dataset_ids=allowed_dataset_ids,
        trace_callback=trace_callback,
    )
    dataset_resolution = dataset_search.get("dataset_resolution") or {}
    dataset_consistency = dataset_search.get("consistency_check") or {}
    if dataset_consistency.get("status") != "passed" or dataset_resolution.get("status") != "resolved":
        code = dataset_consistency.get("code") or "DATASET_INTENT_MISMATCH"
        reason = dataset_consistency.get("reason") or dataset_resolution.get(
            "reason",
            "数据集意图校验未通过",
        )
        execution = _rejected_execution(code, reason)
        result = {
            "status": "rejected",
            "query": clean_query,
            "query_understanding": understanding,
            "dataset_query": dataset_query,
            "dataset_search": dataset_search,
            "dataset_resolution": dataset_resolution,
            "dataset_consistency_check": dataset_consistency,
            "field_resolution": None,
            "instrument_resolution": None,
            "identifier_resolution": None,
            "execution": execution,
            "warnings": list(dataset_search.get("warnings") or []) + [reason],
        }
        return _compatibility_result(
            result,
            compatibility_route=compatibility_route,
            expected_storage_table_name=expected_storage_table_name,
        )

    storage_table_name = dataset_resolution.get("storage_table_name")
    adapter_spec = ADAPTER_REGISTRY.get(storage_table_name)

    # 独立页面的 route 只是测试范围约束。必须在字段目录、instrument_master、
    # instrument_identifier 和业务表查询之前校验目录结果，避免先执行了另一条路线
    # 再在响应层“补救”导致越权查询。
    if compatibility_route is not None and expected_storage_table_name is not None:
        compatibility_check = _run_traced(
            trace_callback,
            "compatibility_route_check",
            {
                "requested_route": compatibility_route,
                "expected_storage_table_name": expected_storage_table_name,
                "selected_storage_table_name": storage_table_name,
            },
            lambda: {
                "status": "passed"
                if storage_table_name == expected_storage_table_name
                else "rejected",
                "reason": (
                    "数据集与独立页面范围一致"
                    if storage_table_name == expected_storage_table_name
                    else "用户问题对应的数据集与当前独立页面范围不一致"
                ),
            },
            lambda value: value,
        )
        if compatibility_check.get("status") != "passed":
            reason = str(compatibility_check.get("reason"))
            result = {
                "status": "rejected",
                "query": clean_query,
                "query_understanding": understanding,
                "dataset_query": dataset_query,
                "dataset_search": dataset_search,
                "dataset_resolution": dataset_resolution,
                "dataset_consistency_check": dataset_consistency,
                "field_resolution": None,
                "instrument_resolution": None,
                "identifier_resolution": None,
                "execution": _rejected_execution("ROUTE_DATASET_MISMATCH", reason),
                "warnings": [reason],
            }
            return _compatibility_result(
                result,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_storage_table_name,
            )

    if adapter_spec is None:
        reason = f"数据集目录返回的业务表没有登记适配器：{storage_table_name}"
        execution = _rejected_execution("ADAPTER_NOT_REGISTERED", reason)
        result = {
            "status": "rejected",
            "query": clean_query,
            "query_understanding": understanding,
            "dataset_query": dataset_query,
            "dataset_search": dataset_search,
            "dataset_resolution": dataset_resolution,
            "dataset_consistency_check": dataset_consistency,
            "field_resolution": None,
            "instrument_resolution": None,
            "identifier_resolution": None,
            "execution": execution,
            "warnings": [reason],
        }
        return _compatibility_result(
            result,
            compatibility_route=compatibility_route,
            expected_storage_table_name=expected_storage_table_name,
        )

    adapter_request = _parse_adapter_request(
        storage_table_name,
        understanding,
        row_limit=row_limit,
        start_date_override=start_date_override,
        end_date_override=end_date_override,
        trace_callback=trace_callback,
    )
    if adapter_request and adapter_request.get("status") != "resolved":
        reason = str(adapter_request.get("reason") or "查询参数未通过校验")
        execution = _rejected_execution("QUERY_PARAMETERS_INVALID", reason)
        result = {
            "status": "rejected",
            "query": clean_query,
            "query_understanding": understanding,
            "dataset_query": dataset_query,
            "dataset_search": dataset_search,
            "dataset_resolution": dataset_resolution,
            "dataset_consistency_check": dataset_consistency,
            "request_resolution": adapter_request,
            "field_resolution": None,
            "instrument_resolution": None,
            "identifier_resolution": None,
            "execution": execution,
            "warnings": [reason],
        }
        return _compatibility_result(
            result,
            compatibility_route=compatibility_route,
            expected_storage_table_name=expected_storage_table_name,
        )

    field_resolution = _run_traced(
        trace_callback,
        "dataset_field_catalog",
        {
            "dataset_id": dataset_resolution.get("dataset_id"),
            "storage_table_name": storage_table_name,
            "requested_fields": list(adapter_spec["fields"]),
            "llm": False,
        },
        lambda: resolve_dataset_fields(
            cursor,
            dataset_resolution["dataset_id"],
            storage_table_name,
            list(adapter_spec["fields"]),
        ),
        lambda value: value,
    )
    if field_resolution.get("status") != "resolved":
        reason = str(field_resolution.get("reason") or "字段目录校验未通过")
        execution = _rejected_execution("FIELD_RESOLUTION_FAILED", reason)
        result = {
            "status": "rejected",
            "query": clean_query,
            "query_understanding": understanding,
            "dataset_query": dataset_query,
            "dataset_search": dataset_search,
            "dataset_resolution": dataset_resolution,
            "dataset_consistency_check": dataset_consistency,
            "request_resolution": adapter_request,
            "field_resolution": field_resolution,
            "instrument_resolution": None,
            "identifier_resolution": None,
            "execution": execution,
            "warnings": [reason],
        }
        return _compatibility_result(
            result,
            compatibility_route=compatibility_route,
            expected_storage_table_name=expected_storage_table_name,
        )

    instrument_search: dict[str, Any] | None = None
    selected_identifier: dict[str, Any] | None = None
    if adapter_spec.get("resolves_instrument_master"):
        # 这是“只查标准金融工具”的数据集适配器。它复用同一套金融工具四路检索
        # 和 active 状态校验，但明确跳过 instrument_identifier，因为本路由不需要
        # 供应商代码，只需要返回 instrument_master 的 canonical_symbol。
        subject_text = understanding.get("subject_text")
        subject_search_text = understanding.get("subject_search_text") or subject_text
        if not subject_text or not subject_search_text:
            reason = "查询理解没有提取到需要标准化的金融工具主体"
            execution = _rejected_execution("INSTRUMENT_NOT_FOUND", reason)
            result = {
                "status": "rejected",
                "query": clean_query,
                "query_understanding": understanding,
                "dataset_query": dataset_query,
                "dataset_search": dataset_search,
                "dataset_resolution": dataset_resolution,
                "dataset_consistency_check": dataset_consistency,
                "request_resolution": adapter_request,
                "field_resolution": field_resolution,
                "instrument_search": None,
                "instrument_resolution": None,
                "identifier_resolution": None,
                "execution": execution,
                "warnings": [reason],
            }
            return _compatibility_result(
                result,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_storage_table_name,
            )

        instrument_search = search_instrument_documents(
            cursor,
            subject_search_text,
            limit=limit,
            use_embedding=use_embedding,
            use_candidate_llm=use_candidate_llm,
            allowed_instrument_types=adapter_spec["allowed_instrument_types"],
            resolve_identifier=False,
            trace_callback=trace_callback,
        )
        model_selection = instrument_search.get("model_selection") or {}
        if model_selection.get("decision") != "select":
            reason = model_selection.get("reason") or "金融工具候选没有通过模型筛选"
            code = (
                "INSTRUMENT_AMBIGUOUS"
                if model_selection.get("decision") == "needs_confirmation"
                else "INSTRUMENT_NOT_FOUND"
            )
            execution = _rejected_execution(code, reason)
            result = {
                "status": "rejected",
                "query": clean_query,
                "query_understanding": understanding,
                "dataset_query": dataset_query,
                "dataset_search": dataset_search,
                "dataset_resolution": dataset_resolution,
                "dataset_consistency_check": dataset_consistency,
                "request_resolution": adapter_request,
                "field_resolution": field_resolution,
                "instrument_search": instrument_search,
                "instrument_resolution": instrument_search,
                "identifier_resolution": None,
                "execution": execution,
                "warnings": [reason],
            }
            return _compatibility_result(
                result,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_storage_table_name,
            )
    elif adapter_spec["requires_instrument_identity"]:
        subject_text = understanding.get("subject_text")
        subject_search_text = understanding.get("subject_search_text") or subject_text
        if not subject_text or not subject_search_text:
            reason = "查询理解没有提取到需要确认的金融工具或宏观指标主体"
            execution = _rejected_execution("INSTRUMENT_NOT_FOUND", reason)
            result = {
                "status": "rejected",
                "query": clean_query,
                "query_understanding": understanding,
                "dataset_query": dataset_query,
                "dataset_search": dataset_search,
                "dataset_resolution": dataset_resolution,
                "dataset_consistency_check": dataset_consistency,
                "request_resolution": adapter_request,
                "field_resolution": field_resolution,
                "instrument_resolution": None,
                "identifier_resolution": None,
                "execution": execution,
                "warnings": [reason],
            }
            return _compatibility_result(
                result,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_storage_table_name,
            )

        instrument_search = search_instrument_documents(
            cursor,
            subject_search_text,
            limit=limit,
            use_embedding=use_embedding,
            use_candidate_llm=use_candidate_llm,
            provider=query_provider,
            identifier_as_of_date=_identifier_as_of_date(
                storage_table_name,
                adapter_request,
            ),
            allowed_instrument_types=adapter_spec["allowed_instrument_types"],
            trace_callback=trace_callback,
        )
        model_selection = instrument_search.get("model_selection") or {}
        identifier_resolution = instrument_search.get("identifier_resolution") or {}
        if model_selection.get("decision") != "select":
            reason = model_selection.get("reason") or "金融工具候选没有通过模型筛选"
            execution = _rejected_execution("INSTRUMENT_INTENT_MISMATCH", reason)
            result = {
                "status": "rejected",
                "query": clean_query,
                "query_understanding": understanding,
                "dataset_query": dataset_query,
                "dataset_search": dataset_search,
                "dataset_resolution": dataset_resolution,
                "dataset_consistency_check": dataset_consistency,
                "request_resolution": adapter_request,
                "field_resolution": field_resolution,
                "instrument_search": instrument_search,
                "instrument_resolution": instrument_search,
                "identifier_resolution": identifier_resolution,
                "execution": execution,
                "warnings": [reason],
            }
            return _compatibility_result(
                result,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_storage_table_name,
            )
        if identifier_resolution.get("status") != "resolved":
            reason = "没有唯一有效的 instrument_identifier，已停止业务查询"
            execution = _rejected_execution("IDENTIFIER_NOT_EFFECTIVE", reason)
            result = {
                "status": "rejected",
                "query": clean_query,
                "query_understanding": understanding,
                "dataset_query": dataset_query,
                "dataset_search": dataset_search,
                "dataset_resolution": dataset_resolution,
                "dataset_consistency_check": dataset_consistency,
                "request_resolution": adapter_request,
                "field_resolution": field_resolution,
                "instrument_search": instrument_search,
                "instrument_resolution": instrument_search,
                "identifier_resolution": identifier_resolution,
                "execution": execution,
                "warnings": [reason],
            }
            return _compatibility_result(
                result,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_storage_table_name,
            )

        selected_identifier = identifier_resolution.get("selected") or {}
        if (
            dataset_resolution.get("provider")
            and selected_identifier.get("provider")
            and dataset_resolution.get("provider")
            != selected_identifier.get("provider")
        ):
            reason = "dataset_catalog.provider 与 instrument_identifier.provider 不一致"
            execution = _rejected_execution("DATASET_PROVIDER_MISMATCH", reason)
            result = {
                "status": "rejected",
                "query": clean_query,
                "query_understanding": understanding,
                "dataset_query": dataset_query,
                "dataset_search": dataset_search,
                "dataset_resolution": dataset_resolution,
                "dataset_consistency_check": dataset_consistency,
                "request_resolution": adapter_request,
                "field_resolution": field_resolution,
                "instrument_search": instrument_search,
                "instrument_resolution": instrument_search,
                "identifier_resolution": identifier_resolution,
                "execution": execution,
                "warnings": [reason],
            }
            return _compatibility_result(
                result,
                compatibility_route=compatibility_route,
                expected_storage_table_name=expected_storage_table_name,
            )

    # 统一适配器执行入口。每个适配器仍然使用自己的结构化过滤逻辑，但不再
    # 由自然语言路线枚举决定，也不允许模型提交任意表名或 SQL。
    execution_input = {
        "adapter": adapter_spec["adapter"],
        "storage_table_name": storage_table_name,
        "dataset_id": dataset_resolution.get("dataset_id"),
        "fields": [field.get("physical_column_name") for field in field_resolution.get("fields", [])],
        "provider": selected_identifier.get("provider") if selected_identifier else query_provider,
    }

    def execute_adapter() -> dict[str, Any]:
        """调用目录已经选中的具体业务适配器。"""

        if storage_table_name == LATEST_PRICES_TABLE:
            selected_tool = (instrument_search or {}).get("model_selection") or {}
            return query_latest_prices(
                cursor,
                selected_tool["instrument_id"],
                selected_identifier["provider"],
                selected_identifier["identifier"],
                dataset_resolution,
                field_resolution,
                limit=1,
            )
        if storage_table_name == INSTRUMENT_MASTER_TABLE:
            selected_tool = (instrument_search or {}).get("model_selection") or {}
            return query_instrument_master(
                cursor,
                selected_tool["instrument_id"],
                dataset_resolution,
                field_resolution,
                limit=1,
            )
        if storage_table_name == MARKET_BARS_TABLE:
            selected_tool = (instrument_search or {}).get("model_selection") or {}
            return query_market_bars(
                cursor,
                selected_tool["instrument_id"],
                selected_identifier["provider"],
                selected_identifier["identifier"],
                dataset_resolution,
                field_resolution,
                start_date=date.fromisoformat(adapter_request["start_date"]),
                end_date=date.fromisoformat(adapter_request["end_date"]),
                frequency=adapter_request.get("frequency") or MARKET_BARS_FREQUENCY,
                limit=row_limit,
            )
        if storage_table_name == "macro_observations":
            selected_tool = (instrument_search or {}).get("model_selection") or {}
            return query_macro_observations(
                cursor,
                selected_tool["instrument_id"],
                selected_identifier["provider"],
                selected_identifier["identifier"],
                dataset_resolution,
                field_resolution,
                start_date=(
                    date.fromisoformat(adapter_request["start_date"])
                    if adapter_request and adapter_request.get("start_date")
                    else None
                ),
                end_date=(
                    date.fromisoformat(adapter_request["end_date"])
                    if adapter_request and adapter_request.get("end_date")
                    else None
                ),
                frequency=adapter_request.get("frequency") if adapter_request else None,
                limit=adapter_request.get("row_limit", row_limit)
                if adapter_request
                else row_limit,
            )
        if storage_table_name == NEWS_TABLE:
            subject = (
                understanding.get("subject_search_text")
                or understanding.get("subject_text")
                or understanding.get("query_rewrite")
                or clean_query
            )
            news_terms = understanding.get("search_terms") or []
            news_query = " ".join(
                [str(subject), *[str(term) for term in news_terms]]
            ).strip()
            news_search = search_news_documents(
                cursor,
                news_query,
                limit=None,
                use_embedding=use_embedding,
                provider=query_provider,
                start_date=(
                    date.fromisoformat(adapter_request["start_date"])
                    if adapter_request and adapter_request.get("start_date")
                    else None
                ),
                end_date=(
                    date.fromisoformat(adapter_request["end_date"])
                    if adapter_request and adapter_request.get("end_date")
                    else None
                ),
                trace_callback=trace_callback,
            )
            result_holder["news_search"] = news_search
            return query_news_articles(
                cursor,
                news_search.get("candidates") or [],
                dataset_resolution,
                field_resolution,
                limit=None,
                start_date=(
                    date.fromisoformat(adapter_request["start_date"])
                    if adapter_request and adapter_request.get("start_date")
                    else None
                ),
                end_date=(
                    date.fromisoformat(adapter_request["end_date"])
                    if adapter_request and adapter_request.get("end_date")
                    else None
                ),
            )
        raise RuntimeError(f"适配器注册表缺少业务实现：{storage_table_name}")

    result_holder: dict[str, Any] = {}
    execution = _run_traced(
        trace_callback,
        "business_adapter_query",
        execution_input,
        execute_adapter,
        lambda value: value,
    )
    result = {
        "status": "success" if execution.get("status") in {"resolved", "not_found"} else "rejected",
        "query": clean_query,
        "query_understanding": understanding,
        "dataset_query": dataset_query,
        "dataset_search": dataset_search,
        "dataset_resolution": dataset_resolution,
        "dataset_consistency_check": dataset_consistency,
        "request_resolution": adapter_request,
        "field_resolution": field_resolution,
        "instrument_search": instrument_search,
        "instrument_resolution": instrument_search,
        "identifier_resolution": (
            instrument_search.get("identifier_resolution") if instrument_search else None
        ),
        "execution": execution,
        "adapter": adapter_spec["adapter"],
        "warnings": list(dataset_search.get("warnings") or [])
        + list((instrument_search or {}).get("warnings") or [])
        + list((result_holder.get("news_search") or {}).get("warnings") or []),
    }
    if storage_table_name == LATEST_PRICES_TABLE:
        result["price_result"] = execution
    elif storage_table_name == MARKET_BARS_TABLE:
        result["market_bars_result"] = execution
    elif storage_table_name == "macro_observations":
        result["macro_observations_result"] = execution
    elif storage_table_name == NEWS_TABLE:
        result["news_search"] = result_holder.get("news_search")
        result["news_result"] = execution
    return _compatibility_result(
        result,
        compatibility_route=compatibility_route,
        expected_storage_table_name=expected_storage_table_name,
    )
