"""查询意图枚举和 latest_prices 路线闸门测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import latest_price_pipeline  # noqa: E402
from query_intent import QueryRoute, validate_query_intent_result  # noqa: E402
from query_parser import (  # noqa: E402
    validate_query_parse_result,
    validate_query_understanding_result,
)


class UnusedCursor:
    """路线不匹配时使用的哨兵游标；被访问就说明越过了路线闸门。"""

    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("路线不匹配时不应进入金融工具数据库检索")


def test_query_intent_accepts_only_controlled_route_values() -> None:
    """合法模型输出应被转换为枚举值对应的字符串。"""

    result = validate_query_intent_result(
        {
            "route": QueryRoute.LATEST_PRICES.value,
            "confidence": 0.98,
            "reason": "用户询问当前报价",
            "unexpected_table": "source.latest_prices",
        }
    )

    assert result == {
        "route": "latest_prices",
        "confidence": 0.98,
        "reason": "用户询问当前报价",
        "instrument_text": None,
        "provider_text": None,
        "time_expression": None,
        "request_text": None,
    }


def test_query_intent_rejects_unknown_route() -> None:
    """模型不能通过返回任意字符串扩展系统路线。"""

    with pytest.raises(ValueError, match="未知路线"):
        validate_query_intent_result(
            {"route": "source_latest_prices", "confidence": 0.9, "reason": "猜测"}
        )


def test_query_parser_keeps_original_subject_and_time_spans() -> None:
    """解析结果中的工具和时间必须来自用户原文，不能提前翻译或标准化。"""

    result = validate_query_parse_result(
        {
            "route": "market_bars",
            "instrument_text": "EURUSD",
            "instrument_search_text": "EURUSD",
            "provider_text": "LSEG",
            "time_expression": "最近一个月",
            "request_text": "日K线",
            "confidence": 0.99,
            "reason": "历史行情",
        },
        original_query="查询 EURUSD 最近一个月的 LSEG 日K线",
    )

    assert result["instrument_text"] == "EURUSD"
    assert result["instrument_search_text"] == "EURUSD"
    assert result["provider_text"] == "LSEG"
    assert result["time_expression"] == "最近一个月"
    assert result["request_text"] == "日K线"


def test_query_parser_rejects_normalized_subject_not_in_original_query() -> None:
    """模型不能把用户原文 EURUSD 擅自改写成 EUR/USD 后交给检索。"""

    with pytest.raises(ValueError, match="instrument_text"):
        validate_query_parse_result(
            {
                "route": "latest_prices",
                "instrument_text": "EUR/USD",
                "confidence": 0.99,
                "reason": "最新价格",
            },
            original_query="查询 EURUSD 的最新价格",
        )


def test_query_parser_keeps_macro_indicator_as_query_subject() -> None:
    """宏观路线的主体是指标文本，不应因为它不是传统金融工具而被清空。"""

    result = validate_query_parse_result(
        {
            "route": "macro_observations",
            "instrument_text": "美国 CPI",
            "instrument_search_text": "US CPI",
            "request_text": "最新值",
            "confidence": 0.99,
            "reason": "用户查询宏观指标",
        },
        original_query="查询美国 CPI 最新值",
    )

    assert result["instrument_text"] == "美国 CPI"
    assert result["instrument_search_text"] == "US CPI"


def test_unified_query_understanding_does_not_return_route_or_database_objects() -> None:
    """统一入口只保留查询理解文本，数据集和字段由目录阶段决定。"""

    result = validate_query_understanding_result(
        {
            "subject_text": "EURUSD",
            "subject_search_text": "EURUSD EUR/USD",
            "provider_text": None,
            "time_expression": "最近一个月",
            "request_text": "相关新闻",
            "query_rewrite": "EUR/USD related financial articles",
            "search_terms": ["EUR/USD", "euro dollar"],
            "route": "news_articles",
            "dataset_id": "LSEG_NEWS",
            "storage_table_name": "news_articles",
            "sql": "SELECT * FROM source.news_articles",
            "confidence": 0.97,
            "reason": "用户询问相关新闻",
        },
        original_query="查询 EURUSD 最近一个月的相关新闻",
    )

    assert result["subject_text"] == "EURUSD"
    assert result["query_rewrite"] == "EUR/USD related financial articles"
    assert result["search_terms"] == ["EUR/USD", "euro dollar"]
    assert "route" not in result
    assert "dataset_id" not in result
    assert "storage_table_name" not in result
    assert "sql" not in result


def test_unified_query_understanding_keeps_unspecified_provider_null() -> None:
    """自然语言没有供应商时，统一解析不能替用户补入 LSEG。"""

    result = validate_query_understanding_result(
        {
            "subject_text": "美国 CPI",
            "subject_search_text": "US CPI",
            "provider_text": None,
            "time_expression": None,
            "request_text": "最新值",
            "query_rewrite": "US CPI latest value",
            "search_terms": ["US CPI"],
            "confidence": 0.96,
            "reason": "查询美国 CPI",
        },
        original_query="查询美国 CPI 最新值",
    )

    assert result["provider_text"] is None


def test_route_guard_stops_non_latest_query_before_instrument_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """宏观问题在 latest_prices 页面只完成意图和闸门阶段。"""

    monkeypatch.setattr(
        latest_price_pipeline,
        "recognize_query_intent",
        lambda _query: {
            "route": QueryRoute.MACRO_OBSERVATIONS.value,
            "confidence": 0.97,
            "reason": "用户询问宏观指标",
            "instrument_text": None,
            "provider_text": None,
            "time_expression": None,
            "request_text": "宏观指标",
        },
    )
    events: list[dict[str, object]] = []

    result = latest_price_pipeline.search_latest_price_route(
        UnusedCursor(),
        "查询美国 CPI",
        requested_route=QueryRoute.LATEST_PRICES,
        trace_callback=events.append,
    )

    assert result["route_guard"]["accepted"] is False
    assert result["query_intent"]["route"] == "macro_observations"
    assert result["candidates"] == []
    assert [event["stage"] for event in events] == [
        "query_parse",
        "query_parse",
        "route_guard",
        "route_guard",
    ]


def test_route_guard_allows_latest_query_to_enter_instrument_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """latest_prices 意图通过闸门后才调用已有金融工具检索入口。"""

    monkeypatch.setattr(
        latest_price_pipeline,
        "recognize_query_intent",
        lambda _query: {
            "route": QueryRoute.LATEST_PRICES.value,
            "confidence": 0.99,
            "reason": "用户询问最新价格",
            "instrument_text": "EURUSD",
            "provider_text": None,
            "time_expression": None,
            "request_text": "最新价格",
        },
    )

    observed: dict[str, object] = {}

    def fake_search(_cursor: object, query: str, **kwargs: object) -> dict[str, object]:
        observed["query"] = query
        observed["kwargs"] = kwargs
        return {
            "query": query,
            "methods": {},
            "warnings": [],
            "master_resolution": {},
            "candidates": [],
            "model_selection": None,
            "identifier_resolution": None,
        }

    monkeypatch.setattr(latest_price_pipeline, "search_instrument_documents", fake_search)

    observed_dataset: dict[str, object] = {}

    def fake_dataset(_cursor: object, query: str, **kwargs: object) -> dict[str, object]:
        observed_dataset["query"] = query
        return {
            "query": query,
            "dataset_resolution": {
                "status": "resolved",
                "dataset_id": "LSEG_SPOT_PRICE",
                "storage_table_name": "latest_prices",
                "provider": "LSEG",
            },
        }

    monkeypatch.setattr(latest_price_pipeline, "search_dataset_documents", fake_dataset)

    result = latest_price_pipeline.search_latest_price_route(
        object(),
        "查询 EURUSD 的最新价格",
        requested_route=QueryRoute.LATEST_PRICES,
    )

    assert observed["query"] == "EURUSD"
    assert result["route"] == "latest_prices"
    assert result["route_guard"]["accepted"] is True


def test_latest_route_continues_from_dataset_to_fields_and_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """latest_prices 路线应在数据集确认后继续字段目录和业务表查询。"""

    monkeypatch.setattr(
        latest_price_pipeline,
        "recognize_query_intent",
        lambda _query: {
            "route": QueryRoute.LATEST_PRICES.value,
            "confidence": 0.99,
            "reason": "用户询问最新价格",
            "instrument_text": "EURUSD",
            "provider_text": None,
            "time_expression": None,
            "request_text": "最新价格",
        },
    )
    monkeypatch.setattr(
        latest_price_pipeline,
        "search_instrument_documents",
        lambda *_args, **_kwargs: {
            "query": "查询 EURUSD 的最新价格",
            "methods": {},
            "warnings": [],
            "master_resolution": {},
            "candidates": [],
            "model_selection": {
                "decision": "select",
                "instrument_id": "FX_EURUSD",
                "candidate": {"canonical_symbol": "EUR/USD"},
            },
            "identifier_resolution": {
                "status": "resolved",
                "selected": {"provider": "LSEG", "identifier": "EUR="},
            },
        },
    )
    observed_dataset: dict[str, object] = {}

    def fake_dataset(_cursor: object, query: str, **kwargs: object) -> dict[str, object]:
        observed_dataset["query"] = query
        return {
            "query": query,
            "dataset_resolution": {
                "status": "resolved",
                "dataset_id": "LSEG_SPOT_PRICE",
                "storage_table_name": "latest_prices",
                "provider": "LSEG",
            },
        }

    monkeypatch.setattr(latest_price_pipeline, "search_dataset_documents", fake_dataset)
    monkeypatch.setattr(
        latest_price_pipeline,
        "resolve_dataset_fields",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "fields": [{"field_name": "price_time", "physical_column_name": "price_time"}],
        },
    )
    monkeypatch.setattr(
        latest_price_pipeline,
        "query_latest_prices",
        lambda *_args, **_kwargs: {
            "status": "resolved",
            "rows": [{"price_time": "2026-08-10T03:53:45+00:00", "last": "1.152800"}],
            "row_count": 1,
        },
    )

    events: list[dict[str, object]] = []
    result = latest_price_pipeline.search_latest_price_route(
        object(),
        "查询 EURUSD 的最新价格",
        requested_route=QueryRoute.LATEST_PRICES,
        trace_callback=events.append,
    )

    assert result["dataset_resolution"]["dataset_id"] == "LSEG_SPOT_PRICE"
    assert observed_dataset["query"] == "最新价格 latest spot price quote"
    assert result["field_resolution"]["status"] == "resolved"
    assert result["price_result"]["rows"][0]["last"] == "1.152800"
    assert [event["stage"] for event in events][-4:] == [
        "dataset_field_catalog",
        "dataset_field_catalog",
        "latest_prices_query",
        "latest_prices_query",
    ]
