"""统一查询入口和目录驱动兼容边界测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "front"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import server  # noqa: E402
from unified_search_pipeline import run_unified_query  # noqa: E402


class _CursorContext:
    """为 FastAPI 服务测试提供最小的 cursor 上下文管理器。"""

    def __enter__(self) -> "_CursorContext":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _ConnectionContext:
    """模拟只读数据库连接，不执行真实数据库访问。"""

    def __enter__(self) -> "_ConnectionContext":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def set_session(self, **_: Any) -> None:
        return None

    def cursor(self) -> _CursorContext:
        return _CursorContext()


def _resolved_result(dataset_id: str = "LSEG_SPOT_PRICE") -> dict[str, Any]:
    """构造统一编排器已经完成目录确认的最小响应。"""

    table = {
        "LSEG_SPOT_PRICE": "latest_prices",
        "LSEG_MARKET_BARS": "market_bars",
        "LSEG_MACRO": "macro_observations",
        "LSEG_NEWS": "news_articles",
    }[dataset_id]
    return {
        "status": "success",
        "query": "测试统一查询",
        "dataset_resolution": {
            "status": "resolved",
            "dataset_id": dataset_id,
            "storage_table_name": table,
            "provider": "LSEG",
        },
        "dataset_consistency_check": {
            "status": "passed",
            "selected_dataset_id": dataset_id,
            "candidate_dataset_ids": [dataset_id],
            "reason": "测试通过",
        },
        "execution": {
            "status": "resolved",
            "adapter": table,
            "rows": [],
            "row_count": 0,
        },
        "adapter": table,
    }


def test_unified_search_is_driven_by_dataset_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """统一接口不接收 route，并把请求交给目录驱动编排器。"""

    calls: dict[str, Any] = {}

    def fake_run_unified_query(cursor: Any, query: str, **kwargs: Any) -> dict[str, Any]:
        calls["cursor"] = cursor
        calls["query"] = query
        calls["kwargs"] = kwargs
        return _resolved_result()

    monkeypatch.setattr(server.psycopg2, "connect", lambda **_: _ConnectionContext())
    monkeypatch.setattr(server, "run_unified_query", fake_run_unified_query)

    request = server.UnifiedSearchRequest(query="测试统一查询")
    result = server.run_unified_search(request)

    assert calls["query"] == "测试统一查询"
    assert "compatibility_route" not in calls["kwargs"]
    assert "expected_storage_table_name" not in calls["kwargs"]
    assert calls["kwargs"]["use_candidate_llm"] is True
    assert result["interface"] == "unified_search"
    assert result["routing"] == {
        "mode": "dataset_catalog",
        "dataset_id": "LSEG_SPOT_PRICE",
        "storage_table_name": "latest_prices",
        "adapter": "latest_prices",
        "reason": "测试通过",
    }


def test_unified_search_trace_is_forwarded_to_directory_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """统一入口不制造旧的路线识别阶段，阶段由统一编排器真实产生。"""

    events: list[dict[str, Any]] = []

    def fake_run_unified_query(cursor: Any, query: str, **kwargs: Any) -> dict[str, Any]:
        callback = kwargs["trace_callback"]
        callback(
            {
                "stage": "query_understanding",
                "status": "completed",
                "input": {"query": query},
                "output": {"subject_text": "EURUSD"},
                "duration_ms": 1,
                "error": None,
            }
        )
        return _resolved_result()

    monkeypatch.setattr(server.psycopg2, "connect", lambda **_: _ConnectionContext())
    monkeypatch.setattr(server, "run_unified_query", fake_run_unified_query)

    result = server.run_unified_search(
        server.UnifiedSearchRequest(query="测试统一查询"),
        trace_callback=events.append,
    )

    assert [event["stage"] for event in events] == ["query_understanding"]
    assert result["routing"]["mode"] == "dataset_catalog"
    assert "recognized_route" not in result["routing"]


def test_compatibility_route_mismatch_stops_before_fields_and_instruments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """独立页面发现另一数据集时，不能越过页面范围继续查字段或主数据。"""

    calls = {"fields": 0, "instruments": 0}
    understanding = {
        "confidence": 0.99,
        "reason": "测试理解",
        "subject_text": "EURUSD",
        "subject_search_text": "EURUSD",
        "provider_text": None,
        "time_expression": None,
        "request_text": "相关新闻",
        "query_rewrite": "EUR/USD related news",
        "search_terms": ["EUR/USD"],
    }

    monkeypatch.setattr(
        "unified_search_pipeline.search_dataset_documents",
        lambda *args, **kwargs: {
            "query": "相关新闻",
            "candidates": [
                {
                    "dataset_id": "LSEG_NEWS",
                    "storage_table_name": "news_articles",
                    "provider": "LSEG",
                    "eligible_for_next_step": True,
                }
            ],
            "model_selection": {"decision": "select", "dataset_id": "LSEG_NEWS"},
            "consistency_check": {
                "status": "passed",
                "selected_dataset_id": "LSEG_NEWS",
                "candidate_dataset_ids": ["LSEG_NEWS"],
            },
            "dataset_resolution": {
                "status": "resolved",
                "dataset_id": "LSEG_NEWS",
                "storage_table_name": "news_articles",
                "provider": "LSEG",
            },
            "warnings": [],
        },
    )

    def fail_fields(*_: Any, **__: Any) -> dict[str, Any]:
        calls["fields"] += 1
        raise AssertionError("路线不一致时不应访问字段目录")

    def fail_instruments(*_: Any, **__: Any) -> dict[str, Any]:
        calls["instruments"] += 1
        raise AssertionError("路线不一致时不应访问金融工具主数据")

    monkeypatch.setattr("unified_search_pipeline.resolve_dataset_fields", fail_fields)
    monkeypatch.setattr("unified_search_pipeline.search_instrument_documents", fail_instruments)

    result = run_unified_query(
        object(),
        "查询 EURUSD 的相关新闻",
        use_embedding=False,
        use_candidate_llm=True,
        query_understanding_override=understanding,
        compatibility_route="latest_prices",
        expected_storage_table_name="latest_prices",
    )

    assert result["status"] == "rejected"
    assert result["route_guard"]["accepted"] is False
    assert result["execution"]["code"] == "ROUTE_DATASET_MISMATCH"
    assert "price_result" not in result
    assert calls == {"fields": 0, "instruments": 0}


def test_compatibility_response_exposes_execution_under_legacy_aliases() -> None:
    """独立页面读取旧字段时，应看到统一 execution 的同一份结果。"""

    from unified_search_pipeline import _compatibility_result

    result = _compatibility_result(
        {
            "status": "success",
            "query_understanding": {
                "subject_text": "EURUSD",
                "subject_search_text": "EURUSD",
                "provider_text": None,
                "time_expression": None,
                "request_text": "最新价格",
            },
            "dataset_search": {"methods": {"exact": 1}},
            "dataset_resolution": {
                "status": "resolved",
                "storage_table_name": "latest_prices",
                "dataset_id": "LSEG_SPOT_PRICE",
            },
            "instrument_search": {
                "methods": {"exact": 1},
                "master_resolution": {"resolved": 1},
                "candidates": [{"instrument_id": "FX_EURUSD"}],
                "model_selection": {"decision": "select"},
            },
            "execution": {"status": "resolved", "rows": [], "row_count": 0},
        },
        compatibility_route="latest_prices",
        expected_storage_table_name="latest_prices",
    )

    assert result["price_result"] is result["execution"]
    assert result["candidates"] == [{"instrument_id": "FX_EURUSD"}]
    assert result["route_guard"]["accepted"] is True
