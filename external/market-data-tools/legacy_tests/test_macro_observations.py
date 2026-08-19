"""``macro_observations`` 路线的参数、字段边界和适配器测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import macro_observations_pipeline as pipeline
from macro_observation_request import parse_macro_observation_request
from macro_observations_adapter import query_macro_observations
from query_intent import QueryRoute


class FakeCursor:
    """记录查询参数并返回预置行，避免单元测试依赖真实数据库。"""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.parameters: tuple[object, ...] | None = None
        self.statement: object | None = None

    def execute(self, statement: object, parameters: tuple[object, ...]) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def _resolved_fields() -> dict[str, object]:
    """构造 LSEG_MACRO 的四个已确认物理字段。"""

    return {
        "status": "resolved",
        "fields": [
            {"field_name": "value", "physical_column_name": "value"},
            {"field_name": "previous_value", "physical_column_name": "previous_value"},
            {"field_name": "forecast_value", "physical_column_name": "forecast_value"},
            {"field_name": "revised_value", "physical_column_name": "revised_value"},
        ],
    }


def test_latest_macro_request_defaults_to_one_row() -> None:
    """没有时间条件时，宏观路线应进入最新值模式。"""

    result = parse_macro_observation_request(
        None,
        "最新值",
        reference_date=date(2026, 8, 10),
    )

    assert result["status"] == "resolved"
    assert result["period_type"] == "latest"
    assert result["row_limit"] == 1
    assert result["start_date"] is None
    assert result["end_date"] is None


def test_macro_request_parses_history_and_frequency() -> None:
    """相对时间和季度条件应转换为业务表可用的日期及频率参数。"""

    result = parse_macro_observation_request(
        "最近一年",
        "季度实际值",
        reference_date=date(2026, 8, 10),
        row_limit=30,
    )

    assert result["status"] == "resolved"
    assert result["period_type"] == "history"
    assert result["frequency"] == "quarterly"
    assert result["start_date"] == "2025-08-10"
    assert result["end_date"] == "2026-08-11"
    assert result["row_limit"] == 30


def test_macro_adapter_filters_by_formal_instrument_id() -> None:
    """适配器必须同时使用 instrument_id、source 和 source_identifier。"""

    cursor = FakeCursor(
        [
            (
                101,
                "US_CPI_YOY",
                "METRIC_US_CPI_YOY",
                "2026-06-30T00:00:00+08:00",
                "quarterly",
                "LSEG",
                "aUSCPIYYR",
                "US",
                "%",
                Decimal("3.86"),
                None,
                Decimal("3.90"),
                None,
            )
        ]
    )
    result = query_macro_observations(
        cursor,
        "METRIC_US_CPI_YOY",
        "LSEG",
        "aUSCPIYYR",
        {
            "status": "resolved",
            "dataset_id": "LSEG_MACRO",
            "storage_table_name": "macro_observations",
            "provider": "LSEG",
        },
        _resolved_fields(),
        limit=1,
    )

    assert result["status"] == "resolved"
    assert set(result["rows"][0]) == {"data", "metadata"}
    assert result["rows"][0]["metadata"]["instrument_id"] == "METRIC_US_CPI_YOY"
    assert result["rows"][0]["data"]["value"] == "3.86"
    assert result["rows"][0]["metadata"]["unit"] == "%"
    assert result["filters"]["linked_rows_only"] is True
    assert cursor.parameters[:3] == (
        "METRIC_US_CPI_YOY",
        "LSEG",
        "aUSCPIYYR",
    )


def test_macro_adapter_does_not_bypass_missing_dataset_fields() -> None:
    """非 LSEG_MACRO 数据集即使共用物理表，也不能借用其字段目录。"""

    cursor = FakeCursor([])
    result = query_macro_observations(
        cursor,
        "IR_US",
        "LSEG",
        "USFOMC=ECI",
        {
            "status": "resolved",
            "dataset_id": "LSEG_INTEREST_RATE",
            "storage_table_name": "macro_observations",
            "provider": "LSEG",
        },
        _resolved_fields(),
        limit=1,
    )

    assert result["status"] == "unsupported_dataset"
    assert cursor.parameters is None


def test_macro_pipeline_stops_interest_rate_until_fields_are_registered(monkeypatch) -> None:
    """当前字段目录不覆盖利率数据时，pipeline 应停止在字段阶段。"""

    monkeypatch.setattr(
        pipeline,
        "recognize_query_intent",
        lambda _query: {
            "route": QueryRoute.MACRO_OBSERVATIONS.value,
            "confidence": 1.0,
            "reason": "test",
            "instrument_text": "美国联邦基金利率",
            "provider_text": None,
            "time_expression": None,
            "request_text": "最新值",
        },
    )
    monkeypatch.setattr(
        pipeline,
        "search_instrument_documents",
        lambda *_args, **_kwargs: {
            "query": "美国联邦基金利率",
            "methods": {},
            "warnings": [],
            "master_resolution": {"resolved": 1, "inactive": 0, "not_found": 0},
            "candidates": [
                {
                    "instrument_id": "IR_US",
                    "instrument_type": "INTEREST_RATE",
                    "canonical_symbol": "US_FEDFUNDS",
                    "master_name": "US Fed Funds Target Rate",
                    "status": "active",
                }
            ],
            "model_selection": {
                "decision": "select",
                "instrument_id": "IR_US",
                "candidate": {
                    "instrument_id": "IR_US",
                    "instrument_type": "INTEREST_RATE",
                    "canonical_symbol": "US_FEDFUNDS",
                    "master_name": "US Fed Funds Target Rate",
                },
            },
            "identifier_resolution": {
                "status": "resolved",
                "selected": {
                    "instrument_id": "IR_US",
                    "provider": "LSEG",
                    "identifier": "USFOMC=ECI",
                },
            },
        },
    )
    monkeypatch.setattr(
        pipeline,
        "search_dataset_documents",
        lambda *_args, **_kwargs: {
            "query": "最新值 central bank policy interest rate",
            "warnings": [],
            "methods": {},
            "catalog_resolution": {"resolved": 1},
            "candidates": [],
            "model_selection": {"decision": "select", "dataset_id": "LSEG_INTEREST_RATE"},
            "dataset_resolution": {
                "status": "resolved",
                "dataset_id": "LSEG_INTEREST_RATE",
                "storage_table_name": "macro_observations",
                "provider": "LSEG",
            },
        },
    )

    result = pipeline.search_macro_observations_route(
        FakeCursor([]),
        "查询美国联邦基金利率最新值",
        use_embedding=False,
        use_candidate_llm=True,
    )

    assert result["dataset_resolution"]["dataset_id"] == "LSEG_INTEREST_RATE"
    assert result["field_resolution"]["status"] == "unsupported_dataset"
    assert result["macro_observations_result"]["status"] == "unsupported_dataset"
