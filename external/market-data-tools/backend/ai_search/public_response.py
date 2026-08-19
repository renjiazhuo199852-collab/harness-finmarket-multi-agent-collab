"""构造 AI Search 对外公开的精简响应。

查询编排器为了支持测试前端和故障定位，会生成完整的内部结果，其中包含数据集
候选、模型判断、字段目录、金融工具校验、过滤条件和阶段信息。这些内容属于服务
内部实现，不应该默认暴露给业务调用方。本模块把内部结果转换为稳定的公开协议，
让四条业务路线统一返回 ``status`` 和 ``data``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


_SUCCESS_EXECUTION_STATUSES = frozenset({"resolved", "not_found"})
_NESTED_BUSINESS_ROWS = frozenset({"macro_observations", "news_articles"})


def _business_rows(
    execution: Mapping[str, Any],
    adapter_override: str | None = None,
) -> list[dict[str, Any]]:
    """只提取业务结果行，不把查询元数据带给公开接口。

    最新价格和历史行情适配器本身返回扁平业务行。宏观指标和新闻适配器为了在
    内部链路中区分业务值与记录元数据，返回 ``{"data": ..., "metadata": ...}``
    结构；公开协议只保留其中的 ``data``，因此不会输出 metric_id、发布时间、
    来源、匹配分数等内部或记录元数据。
    """

    raw_rows = execution.get("rows")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        return []

    # 统一入口通常把 adapter 放在 execution 内；四个兼容独立入口为了兼容旧页面，
    # 也可能只在完整结果顶层保留 adapter。两处都属于服务端受控值，不能让响应层
    # 因为入口差异而把宏观或新闻 metadata 泄露到公开 data 中。
    adapter = str(adapter_override or execution.get("adapter") or "")
    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            continue
        if adapter in _NESTED_BUSINESS_ROWS:
            business_data = raw_row.get("data")
            if isinstance(business_data, Mapping):
                rows.append(dict(business_data))
            continue
        rows.append(dict(raw_row))
    return rows


def _rejection_code(result: Mapping[str, Any], execution: Mapping[str, Any]) -> str:
    """按目录校验、适配器执行和统一编排器顺序提取公开错误码。"""

    direct_code = execution.get("code")
    if direct_code:
        return str(direct_code)

    consistency = result.get("dataset_consistency_check")
    if isinstance(consistency, Mapping) and consistency.get("code"):
        return str(consistency["code"])

    dataset_search = result.get("dataset_search")
    if isinstance(dataset_search, Mapping):
        nested_consistency = dataset_search.get("consistency_check")
        if isinstance(nested_consistency, Mapping) and nested_consistency.get("code"):
            return str(nested_consistency["code"])

    return "QUERY_REJECTED"


def _rejection_message(result: Mapping[str, Any], execution: Mapping[str, Any]) -> str:
    """提取面向调用方的停止原因，不返回内部候选和模型原始输出。"""

    reason = execution.get("reason")
    if reason:
        return str(reason)

    warnings = result.get("warnings")
    if isinstance(warnings, Sequence) and not isinstance(warnings, (str, bytes)) and warnings:
        return str(warnings[-1])

    return "查询未完成，服务已停止后续业务表访问"


def build_public_response(result: Mapping[str, Any]) -> dict[str, Any]:
    """把完整内部查询结果转换为统一的公开业务响应。

    成功和无结果都使用 ``status=success``，调用方只需要处理 ``data`` 数组；目录
    不一致、字段失败或适配器拒绝等业务停止情况使用 ``status=rejected``，同时保留
    稳定错误码和简短消息。完整内部结果只在调试 SSE 流中继续传递。
    """

    execution = result.get("execution")
    if not isinstance(execution, Mapping):
        return {
            "status": "error",
            "data": [],
            "code": "INTERNAL_RESULT_INVALID",
            "message": "查询服务未生成有效业务结果",
        }

    execution_status = str(execution.get("status") or "")
    if execution_status in _SUCCESS_EXECUTION_STATUSES:
        return {
            "status": "success",
            "data": _business_rows(
                execution,
                str(result.get("adapter")) if result.get("adapter") else None,
            ),
        }

    return {
        "status": "rejected",
        "data": [],
        "code": _rejection_code(result, execution),
        "message": _rejection_message(result, execution),
    }


def _evidence_rows(
    execution: Mapping[str, Any],
    adapter_override: str | None = None,
) -> list[dict[str, Any]]:
    """Return business rows plus approved evidence metadata.

    The normal public response intentionally strips metadata for compatibility
    with the search workbench. Debate evidence needs observation time and
    provenance, so this separate contract preserves only the adapter's
    ``data`` and ``metadata`` objects. Candidate documents, SQL, model output,
    and connection details remain private.
    """
    raw_rows = execution.get("rows")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        return []
    adapter = str(adapter_override or execution.get("adapter") or "")
    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            continue
        if adapter in _NESTED_BUSINESS_ROWS:
            data = raw_row.get("data")
            metadata = raw_row.get("metadata")
            if isinstance(data, Mapping):
                item: dict[str, Any] = {"data": dict(data)}
                if isinstance(metadata, Mapping):
                    item["metadata"] = dict(metadata)
                rows.append(item)
            continue
        rows.append(dict(raw_row))
    return rows


def build_evidence_response(result: Mapping[str, Any]) -> dict[str, Any]:
    """Build the versioned, read-only contract used by FX Debate.

    This endpoint is deliberately separate from :func:`build_public_response`;
    changing the latter would silently alter the existing search clients.
    """
    execution = result.get("execution")
    if not isinstance(execution, Mapping):
        return {
            "status": "error",
            "schema_version": "fx-evidence.v1",
            "data": [],
            "code": "INTERNAL_RESULT_INVALID",
            "message": "查询服务未生成有效业务结果",
        }
    execution_status = str(execution.get("status") or "")
    if execution_status in _SUCCESS_EXECUTION_STATUSES:
        keys = (
            "adapter", "dataset_id", "provider", "identifier", "frequency",
            "start_date", "end_date", "filters", "fields", "row_count",
        )
        metadata = {
            key: execution.get(key)
            for key in keys
            if execution.get(key) is not None
        }
        return {
            "status": "success",
            "schema_version": "fx-evidence.v1",
            "data": _evidence_rows(
                execution,
                str(result.get("adapter")) if result.get("adapter") else None,
            ),
            "meta": metadata,
        }
    return {
        "status": "rejected",
        "schema_version": "fx-evidence.v1",
        "data": [],
        "code": _rejection_code(result, execution),
        "message": _rejection_message(result, execution),
    }


def build_public_error(error_type: str, message: str) -> dict[str, Any]:
    """构造系统异常的统一公开错误响应，并始终提供空 ``data`` 数组。"""

    return {
        "status": "error",
        "data": [],
        "code": error_type or "SERVICE_ERROR",
        "message": message,
    }
