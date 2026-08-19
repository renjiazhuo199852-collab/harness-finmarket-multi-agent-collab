"""Tests for the service-backed FX data query boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.fx_debate.context import build_evidence_context
from src.fx_debate.data_query_agent import (
    AiSearchClient,
    FxDataQueryAgent,
    FxDataServiceError,
)
from src.fx_debate.evidence_factory import FxEvidenceFactory
from src.fx_debate.evidence_sources import AiSearchFxEvidenceSource
from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions


def _context():
    request = ResolvedFxDebateRequest(
        status="resolved",
        asset_class="fx",
        instrument_type="spot",
        pair_class="major",
        canonical_symbol="EURUSD",
        display_symbol="EUR/USD",
        base_currency="EUR",
        quote_currency="USD",
        requested_base_currency="EUR",
        requested_quote_currency="USD",
        inverted=False,
        horizon="2 weeks",
        timeframe="4H/1D",
    )
    return build_evidence_context(
        request,
        RunOptions(
            request_id="data-service-test",
            as_of=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        ),
    )


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_client_sends_only_bounded_natural_language_request() -> None:
    seen: dict[str, object] = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({"status": "success", "schema_version": "fx-evidence.v1", "data": []})

    result = AiSearchClient("http://127.0.0.1:8011", opener=opener).search(
        "market_bars_search",
        "查询 EURUSD 最近一个月的日线行情",
        start_date="2026-07-18",
        end_date="2026-08-18",
        max_rows=80,
    )

    assert result["status"] == "success"
    assert seen["url"] == "http://127.0.0.1:8011/v1/evidence/market_bars_search"
    assert seen["payload"] == {
        "query": "查询 EURUSD 最近一个月的日线行情",
        "max_rows": 80,
        "start_date": "2026-07-18",
        "end_date": "2026-08-18",
    }


def test_client_surfaces_structured_provider_rejection() -> None:
    def opener(_request, **_kwargs):
        return _Response(
            {
                "status": "rejected",
                "schema_version": "fx-evidence.v1",
                "code": "DATASET_NOT_FOUND",
                "message": "未找到匹配的数据集",
                "data": [],
            }
        )

    with pytest.raises(FxDataServiceError, match="未找到匹配") as error:
        AiSearchClient("http://data.test", opener=opener).search(
            "unified_search", "查询 EURUSD 的数据"
        )
    assert error.value.code == "DATASET_NOT_FOUND"


def test_query_agent_builds_four_domain_debate_plan() -> None:
    class _NoopClient:
        pass

    plan = FxDataQueryAgent(_NoopClient()).plan_for_debate(_context())
    assert [item.domain for item in plan] == ["prices", "bars", "macro", "news"]
    assert {item.tool for item in plan} == {"unified_search"}
    assert "EUR/USD" in plan[1].query
    assert plan[1].start_date == _context().market_start_time.date().isoformat()
    assert plan[1].end_date == "2026-08-18"


def test_direct_query_defaults_to_unified_search() -> None:
    seen: list[str] = []

    class _Client:
        def search(self, tool, query, **_kwargs):
            seen.append(tool)
            return {"status": "success", "data": [{"query": query}]}

    result = FxDataQueryAgent(_Client()).query("查询 EURUSD 的最新行情")

    assert seen == ["unified_search"]
    assert result["status"] == "success"


def test_evidence_source_maps_provider_metadata_into_raw_snapshot() -> None:
    responses = {
        "prices": {
            "status": "success",
            "data": [{"price_time": "2026-08-18T11:55:00+00:00", "last": "1.10"}],
            "meta": {"provider": "LSEG", "identifier": "EUR="},
        },
        "bars": {
            "status": "success",
            "data": [{"date": "2026-08-17", "open": "1.09", "high": "1.11", "low": "1.08", "close": "1.10"}],
            "meta": {"provider": "LSEG", "identifier": "EUR=", "frequency": "daily"},
        },
        "macro": {
            "status": "success",
            "data": [
                {
                    "data": {"value": "2.4", "forecast_value": "2.3"},
                    "metadata": {"metric_id": "CPI", "release_time": "2026-08-17", "country": "US", "source": "LSEG"},
                }
            ],
            "meta": {"identifier": "EURUSD"},
        },
        "news": {
            "status": "success",
            "data": [
                {
                    "data": {"title": "EURUSD outlook"},
                    "metadata": {"article_id": "n-1", "publish_time": "2026-08-17", "source": "LSEG"},
                }
            ],
            "meta": {},
        },
    }

    class _Client:
        def search(self, _tool, query, **_kwargs):
            return responses[
                "prices"
                if "最新价格" in query
                else "bars"
                if "日线" in query
                else "macro"
                if "宏观" in query
                else "news"
            ]

    snapshot = AiSearchFxEvidenceSource(client=_Client()).load(_context())
    assert snapshot.source_name == "ai_search"
    assert snapshot.prices[0]["source_identifier"] == "EUR="
    assert snapshot.bars[0]["bar_time"] == "2026-08-17"
    assert snapshot.macro[0]["metric_id"] == "CPI"
    assert snapshot.news[0]["article_id"] == "n-1"
    bundle = FxEvidenceFactory().build(_context(), AiSearchFxEvidenceSource(client=_Client()))
    assert bundle.source_name == "ai_search"


def test_top_level_query_tool_is_opt_in(monkeypatch) -> None:
    from src import tools as tools_package

    monkeypatch.setenv("FX_DATA_SERVICE_ENABLED", "1")
    monkeypatch.setenv("FX_DATA_SERVICE_URL", "http://127.0.0.1:8011")
    previous_cache = tools_package._SUBCLASSES_CACHE
    try:
        tools_package._SUBCLASSES_CACHE = None
        registry = tools_package.build_registry()
    finally:
        tools_package._SUBCLASSES_CACHE = previous_cache
    assert "query_fx_data" in registry.tool_names
