"""market_bars 日线参数、字段计划、适配器和路线编排测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import market_bars_pipeline  # noqa: E402
from market_bar_request import parse_market_bar_request  # noqa: E402
from market_bars_adapter import MARKET_BAR_FIELDS, query_market_bars  # noqa: E402
from query_intent import QueryRoute  # noqa: E402


class MarketBarsCursor:
    """捕获 market_bars 参数化查询，并返回一行日线数据。"""

    def __init__(self) -> None:
        self.parameters: tuple[object, ...] | None = None

    def execute(self, _query: object, parameters: tuple[object, ...]) -> None:
        self.parameters = parameters

    def fetchall(self) -> list[tuple[object, ...]]:
        return [
            (
                date(2026, 7, 1),
                Decimal("1.140000"),
                Decimal("1.150000"),
                Decimal("1.130000"),
                Decimal("1.145000"),
                None,
            )
        ]


def test_market_bar_request_parses_relative_daily_range() -> None:
    """“最近三个月的日 K 线”应解析为日期范围，而不是月频率。"""

    result = parse_market_bar_request(
        "查询 EURUSD 最近三个月的日K线",
        reference_date=date(2026, 8, 10),
    )

    assert result["status"] == "resolved"
    assert result["frequency"] == "daily"
    assert result["start_date"] == "2026-05-12"
    assert result["end_date"] == "2026-08-10"


def test_market_bar_request_rejects_monthly_bar_without_raw_data() -> None:
    """月 K 线不是日线日期范围，当前没有对应原始数据时必须停止。"""

    result = parse_market_bar_request("查询 EURUSD 月K线")

    assert result["status"] == "unsupported"
    assert "月 K 线" in result["reason"]


def test_market_bars_pipeline_uses_request_text_for_frequency_validation(monkeypatch) -> None:
    """模型未提取时间范围时，路线仍应使用 request_text 拒绝月 K 线。"""

    monkeypatch.setattr(
        market_bars_pipeline,
        "recognize_query_intent",
        lambda _query: {
            "route": QueryRoute.MARKET_BARS.value,
            "confidence": 0.99,
            "reason": "用户询问月 K 线",
            "instrument_text": "EURUSD",
            "provider_text": None,
            "time_expression": None,
            "request_text": None,
        },
    )

    result = market_bars_pipeline.search_market_bars_route(
        object(),
        "查询 EURUSD 月K线",
        requested_route=QueryRoute.MARKET_BARS,
        use_embedding=False,
        use_candidate_llm=False,
    )

    assert result["market_bar_request"]["status"] == "unsupported"
    assert "月 K 线" in result["market_bar_request"]["reason"]
    assert result["market_bars_result"]["rows"] == []


def test_market_bars_adapter_uses_daily_filters_and_preserves_decimal() -> None:
    """适配器必须限定供应商、标识、daily 和日期范围，并保留数值文本。"""

    cursor = MarketBarsCursor()
    field_resolution = {
        "status": "resolved",
        "fields": [
            {
                "field_name": field,
                "physical_column_name": field,
                "business_name": field,
            }
            for field in MARKET_BAR_FIELDS
        ],
    }
    dataset_resolution = {
        "status": "resolved",
        "dataset_id": "LSEG_MARKET_BARS",
        "storage_table_name": "market_bars",
        "provider": "LSEG",
        "frequency": "daily",
    }

    result = query_market_bars(
        cursor,
        "FX_EURUSD",
        "LSEG",
        "EUR=",
        dataset_resolution,
        field_resolution,
        date(2026, 7, 1),
        date(2026, 7, 31),
        limit=100,
    )

    assert cursor.parameters == (
        "LSEG",
        "EUR=",
        "daily",
        date(2026, 7, 1),
        date(2026, 7, 31),
        100,
    )
    assert result["status"] == "resolved"
    assert result["rows"][0]["open"] == "1.140000"
    assert result["rows"][0]["volume"] is None


def test_market_bars_pipeline_connects_catalogs_to_business_query(monkeypatch) -> None:
    """路线应按工具、标识、数据集、字段目录顺序进入 market_bars。"""

    monkeypatch.setattr(
        market_bars_pipeline,
        "recognize_query_intent",
        lambda _query: {
            "route": QueryRoute.MARKET_BARS.value,
            "confidence": 0.99,
            "reason": "用户询问历史 K 线",
            "instrument_text": "EURUSD",
            "provider_text": None,
            "time_expression": "2026-07-01 到 2026-07-31",
            "request_text": "日K线",
        },
    )
    observed_instrument: dict[str, object] = {}

    def fake_instrument(_cursor: object, query: str, **kwargs: object) -> dict[str, object]:
        observed_instrument["query"] = query
        return {
            "query": "EURUSD",
            "methods": {},
            "warnings": [],
            "master_resolution": {"resolved": 1},
            "candidates": [{"instrument_id": "FX_EURUSD", "canonical_symbol": "EUR/USD"}],
            "model_selection": {
                "decision": "select",
                "instrument_id": "FX_EURUSD",
                "candidate": {"canonical_symbol": "EUR/USD"},
            },
            "identifier_resolution": {
                "status": "resolved",
                "selected": {"provider": "LSEG", "identifier": "EUR="},
            },
        }

    monkeypatch.setattr(market_bars_pipeline, "search_instrument_documents", fake_instrument)

    observed_dataset: dict[str, object] = {}

    def fake_dataset(_cursor: object, query: str, **kwargs: object) -> dict[str, object]:
        observed_dataset["query"] = query
        return {
            "query": query,
            "candidates": [],
            "warnings": [],
            "model_selection": {"decision": "select"},
            "dataset_resolution": {
                "status": "resolved",
                "dataset_id": "LSEG_MARKET_BARS",
                "storage_table_name": "market_bars",
                "provider": "LSEG",
                "frequency": "daily",
            },
        }
    monkeypatch.setattr(market_bars_pipeline, "search_dataset_documents", fake_dataset)
    monkeypatch.setattr(
        market_bars_pipeline,
        "resolve_dataset_fields",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "fields": [
                {"field_name": field, "physical_column_name": field}
                for field in MARKET_BAR_FIELDS
            ],
        },
    )
    monkeypatch.setattr(
        market_bars_pipeline,
        "query_market_bars",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "rows": [{"date": "2026-07-01", "close": "1.145000"}],
            "row_count": 1,
        },
    )

    events: list[dict[str, object]] = []
    result = market_bars_pipeline.search_market_bars_route(
        object(),
        "查询 EURUSD 2026-07-01 到 2026-07-31 的日K线",
        requested_route=QueryRoute.MARKET_BARS,
        trace_callback=events.append,
    )

    assert result["market_bar_request"]["frequency"] == "daily"
    assert observed_instrument["query"] == "EURUSD"
    assert observed_dataset["query"] == "日K线 OHLCV market bars daily"
    assert result["dataset_resolution"]["dataset_id"] == "LSEG_MARKET_BARS"
    assert result["market_bars_result"]["rows"][0]["close"] == "1.145000"
    assert [event["stage"] for event in events][-4:] == [
        "dataset_field_catalog",
        "dataset_field_catalog",
        "market_bars_query",
        "market_bars_query",
    ]
