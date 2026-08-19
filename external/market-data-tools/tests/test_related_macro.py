"""EURUSD 相关宏观指标关系链路的单元测试。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import sys
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))

from backend.ai_search import macro_observations_adapter  # noqa: E402
from backend.ai_search import instrument_metric_link_adapter  # noqa: E402
from backend.ai_search import unified_search_pipeline  # noqa: E402
from backend.ai_search.public_response import build_public_response  # noqa: E402
from backend.ai_search.query_parser import validate_query_understanding_result  # noqa: E402


class _RelationCursor:
    """为关系解析器模拟数据库返回，不模拟 SQL 业务逻辑。"""

    def __init__(self, rows: list[tuple[object, ...]], provider_mismatch: bool = False) -> None:
        self.rows = rows
        self.provider_mismatch = provider_mismatch
        self.last_query = ""
        self.queries: list[str] = []

    def execute(self, query: object, _parameters: object = None) -> None:
        self.last_query = str(query)
        self.queries.append(self.last_query)

    def fetchall(self) -> list[tuple[object, ...]]:
        if self.provider_mismatch and len(self.queries) == 1:
            return []
        return self.rows

    def fetchone(self) -> tuple[bool]:
        return (self.provider_mismatch,)


class _MacroCursor:
    """为相关宏观适配器提供一条带指标身份的观测行。"""

    def __init__(self) -> None:
        self.executed_query: object | None = None

    def execute(self, query: object, _parameters: object = None) -> None:
        self.executed_query = query

    def fetchall(self) -> list[tuple[object, ...]]:
        return [
            (
                1,
                "US_CPI_YOY",
                None,
                datetime(2026, 8, 1, 8, 0),
                "monthly",
                "LSEG",
                "METRIC_US_CPI_YOY",
                "US",
                "%",
                Decimal("3.2"),
                Decimal("3.1"),
                Decimal("3.15"),
                None,
                "quote_currency",
                1,
            )
        ]


def _field_resolution() -> dict[str, object]:
    """构造已经通过字段目录和物理列校验的宏观字段计划。"""

    names = ("value", "previous_value", "forecast_value", "revised_value")
    return {
        "status": "resolved",
        "fields": [
            {"field_name": name, "physical_column_name": name}
            for name in names
        ],
    }


def _understanding() -> dict[str, object]:
    """返回“与主体相关”查询理解结果，模拟模型已通过协议校验。"""

    return {
        "confidence": 0.99,
        "reason": "用户查询 EURUSD 相关宏观指标",
        "subject_text": "EURUSD",
        "subject_search_text": "EURUSD",
        "provider_text": None,
        "time_expression": None,
        "request_text": "相关宏观指标",
        "query_rewrite": "EUR/USD related macroeconomic indicators",
        "search_terms": ["EUR/USD", "euro area indicators", "US indicators"],
        "relation_scope": "related_to_subject",
    }


def test_query_understanding_relation_scope_is_controlled() -> None:
    """模型只能声明关系语义，不能提交任意关系类型。"""

    result = validate_query_understanding_result(_understanding(), original_query="查询与 EURUSD 相关的宏观指标")
    assert result["relation_scope"] == "related_to_subject"

    invalid = dict(_understanding(), relation_scope="invented_relation")
    try:
        validate_query_understanding_result(invalid, original_query="查询与 EURUSD 相关的宏观指标")
    except ValueError as exc:
        assert "relation_scope" in str(exc)
    else:  # pragma: no cover - 仅用于确保异常断言不会被静默跳过
        raise AssertionError("非法 relation_scope 未被拒绝")


def test_related_macro_adapter_keeps_metric_identity() -> None:
    """相关宏观结果必须把 metric_id 和关系角色放入公开 data。"""

    cursor = _MacroCursor()
    result = macro_observations_adapter.query_related_macro_observations(
        cursor,
        "FX_EURUSD",
        [
            {
                "instrument_id": "FX_EURUSD",
                "metric_id": "US_CPI_YOY",
                "relationship_role": "quote_currency",
                "provider": "LSEG",
            }
        ],
        {
            "status": "resolved",
            "dataset_id": "LSEG_MACRO",
            "storage_table_name": "macro_observations",
        },
        _field_resolution(),
        limit=100,
    )

    assert result["status"] == "resolved"
    public = build_public_response({"adapter": "macro_observations", "execution": result})
    assert public == {
        "status": "success",
        "data": [
            {
                "metric_id": "US_CPI_YOY",
                "relationship_role": "quote_currency",
                "value": "3.2",
                "previous_value": "3.1",
                "forecast_value": "3.15",
                "revised_value": None,
            }
        ],
    }


def test_relation_resolver_uses_metric_and_source_and_rejects_provider_mismatch() -> None:
    """关系解析必须按 metric_id + source 工作，并能区分供应商冲突。"""

    cursor = _RelationCursor(
        [
            (
                "FX_EURUSD",
                "US_CPI_YOY",
                "quote_currency",
                "LSEG",
                "active",
                None,
                None,
                True,
            )
        ],
        provider_mismatch=True,
    )
    result = instrument_metric_link_adapter.resolve_instrument_metric_links(
        cursor,
        "FX_EURUSD",
        provider="BLOOMBERG",
    )
    assert "metric_id = link.metric_id" in cursor.queries[0]
    assert "observation.source = link.provider" in cursor.queries[0]
    assert result["status"] == "provider_mismatch"
    assert result["links"] == []


def test_unified_related_macro_path_skips_instrument_identifier(monkeypatch) -> None:
    """相关宏观路线确认 FX 主体后走关系表，不把 FX 当成宏观工具标识。"""

    dataset_resolution = {
        "status": "resolved",
        "dataset_id": "LSEG_MACRO",
        "storage_table_name": "macro_observations",
        "provider": "LSEG",
    }
    monkeypatch.setattr(
        unified_search_pipeline,
        "search_dataset_documents",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "query": "EURUSD 相关宏观指标",
            "warnings": [],
            "consistency_check": {"status": "passed"},
            "dataset_resolution": dataset_resolution,
        },
    )

    instrument_calls: list[dict[str, object]] = []

    def fake_instrument_search(*_args, **kwargs):
        instrument_calls.append(kwargs)
        return {
            "warnings": [],
            "model_selection": {
                "decision": "select",
                "instrument_id": "FX_EURUSD",
                "candidate": {
                    "instrument_id": "FX_EURUSD",
                    "instrument_type": "FX",
                    "status": "active",
                },
            },
            "identifier_resolution": None,
        }

    monkeypatch.setattr(unified_search_pipeline, "search_instrument_documents", fake_instrument_search)
    monkeypatch.setattr(unified_search_pipeline, "resolve_dataset_fields", lambda *_args: _field_resolution())
    monkeypatch.setattr(
        unified_search_pipeline,
        "resolve_instrument_metric_links",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "links": [
                {
                    "metric_id": "EU_CPI_YOY",
                    "relationship_role": "base_currency",
                    "provider": "LSEG",
                },
                {
                    "metric_id": "US_CPI_YOY",
                    "relationship_role": "quote_currency",
                    "provider": "LSEG",
                },
            ],
        },
    )
    monkeypatch.setattr(
        unified_search_pipeline,
        "query_related_macro_observations",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "adapter": "macro_observations",
            "rows": [
                {
                    "data": {
                        "metric_id": "EU_CPI_YOY",
                        "relationship_role": "base_currency",
                        "value": "2.0",
                    }
                }
            ],
        },
    )
    monkeypatch.setattr(
        unified_search_pipeline,
        "query_macro_observations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应走单指标查询")),
    )

    result = unified_search_pipeline.run_unified_query(
        object(),
        "查询与 EURUSD 相关的宏观指标",
        query_understanding_override=_understanding(),
        use_embedding=False,
        use_candidate_llm=True,
    )
    assert result["status"] == "success"
    assert result["identifier_resolution"] is None
    assert instrument_calls[0]["allowed_instrument_types"] == {"FX"}
    assert instrument_calls[0]["resolve_identifier"] is False


def test_unified_related_macro_without_relation_stops_before_business_query(monkeypatch) -> None:
    """正式关系表没有匹配时返回稳定错误码，不访问宏观业务表。"""

    dataset_resolution = {
        "status": "resolved",
        "dataset_id": "LSEG_MACRO",
        "storage_table_name": "macro_observations",
        "provider": "LSEG",
    }
    monkeypatch.setattr(
        unified_search_pipeline,
        "search_dataset_documents",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "warnings": [],
            "consistency_check": {"status": "passed"},
            "dataset_resolution": dataset_resolution,
        },
    )
    monkeypatch.setattr(
        unified_search_pipeline,
        "search_instrument_documents",
        lambda *_args, **_kwargs: {
            "warnings": [],
            "model_selection": {"decision": "select", "instrument_id": "FX_EURUSD"},
        },
    )
    monkeypatch.setattr(unified_search_pipeline, "resolve_dataset_fields", lambda *_args: _field_resolution())
    monkeypatch.setattr(
        unified_search_pipeline,
        "resolve_instrument_metric_links",
        lambda *_args, **_kwargs: {"status": "not_found", "links": [], "reason": "无关系"},
    )
    query_called = False

    def fail_query(*_args, **_kwargs):
        nonlocal query_called
        query_called = True
        raise AssertionError("关系未找到时不应查询业务表")

    monkeypatch.setattr(unified_search_pipeline, "query_related_macro_observations", fail_query)
    result = unified_search_pipeline.run_unified_query(
        object(),
        "查询与 EURUSD 相关的宏观指标",
        query_understanding_override=_understanding(),
        use_embedding=False,
        use_candidate_llm=True,
    )
    public = build_public_response(result)
    assert public["status"] == "rejected"
    assert public["code"] == "MACRO_RELATION_NOT_FOUND"
    assert query_called is False
