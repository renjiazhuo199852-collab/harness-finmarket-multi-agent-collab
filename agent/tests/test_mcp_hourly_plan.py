"""Regression tests for the MCP daily + hourly FX evidence plan."""

from __future__ import annotations

from datetime import datetime, timezone

from src.fx_debate.context import build_evidence_context
from src.fx_debate.data_query_agent import FxDataQueryAgent
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
        RunOptions(as_of=datetime(2026, 8, 25, tzinfo=timezone.utc)),
    )


def test_debate_plan_requests_daily_and_hourly_raw_bars() -> None:
    plans = FxDataQueryAgent(object()).plan_for_debate(_context())
    bars = [plan for plan in plans if plan.domain.startswith("bars")]

    assert [plan.domain for plan in bars] == ["bars", "bars_hourly"]
    assert bars[0].max_rows >= 250
    assert bars[1].max_rows == 1000
    assert "日线" in bars[0].query
    assert "1H" in bars[1].query


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search(self, tool, query, **kwargs):
        del tool
        self.calls.append((query, kwargs["max_rows"]))
        if "1H" in query:
            return {
                "status": "success",
                "schema_version": "evidence.v1",
                "data": [{"frequency": "hourly", "bar_time": "2026-08-24T00:00:00Z"}],
                "meta": {"provider": "LSEG", "frequency": "hourly"},
            }
        if "日线" in query:
            return {
                "status": "success",
                "schema_version": "evidence.v1",
                "data": [{"frequency": "daily", "bar_time": "2026-08-24T00:00:00Z"}],
                "meta": {"provider": "LSEG", "frequency": "daily"},
            }
        return {
            "status": "success",
            "schema_version": "evidence.v1",
            "data": [],
            "meta": {"provider": "LSEG"},
        }


def test_retrieve_for_debate_merges_daily_and_hourly_rows() -> None:
    client = _Client()
    result = FxDataQueryAgent(client).retrieve_for_debate(_context())

    assert len(client.calls) == 5
    assert result["bars"]["status"] == "success"
    assert [row["frequency"] for row in result["bars"]["data"]] == ["daily", "hourly"]
    assert result["bars"]["meta"]["frequency"] == "mixed"
