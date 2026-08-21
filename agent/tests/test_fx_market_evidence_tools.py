"""FX Debate 行情证据 Tool 的公开契约测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.fx_debate.context import build_evidence_context
from src.fx_debate.analytics import aggregate_four_hour
from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions
from src.fx_debate.store import FxEvidenceStore
from src.tools.fx_debate_tools import (
    GetFxEvidenceByIdsTool,
    GetFxMarketEvidenceTool,
)
from src.tools.validate_fx_output_tool import ValidateFxOutputTool


def test_run_scoped_evidence_tools_are_available_without_database() -> None:
    """MCP-backed runs still need lookup and validation over the frozen store."""
    assert GetFxEvidenceByIdsTool.check_available() is True
    assert ValidateFxOutputTool.check_available() is True


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
            request_id="req-market",
            as_of=datetime(2025, 7, 23, 12, tzinfo=timezone.utc),
        ),
    )


class _FakeReader:
    """在 PostgreSQL Reader 边界提供确定性、含未来行的测试数据。"""

    is_configured = True

    def __init__(self, as_of: datetime) -> None:
        self.as_of = as_of
        self.latest_calls = 0
        self.bar_calls = 0

    def get_latest_prices(self, **kwargs: Any) -> dict[str, Any]:
        self.latest_calls += 1
        return {
            "instrument": {
                "instrument_id": 7,
                "canonical_symbol": "EURUSD",
            },
            "prices": [
                {
                    "price_time": self.as_of + timedelta(minutes=1),
                    "last_price": Decimal("9.9999"),
                    "bid": Decimal("9.9998"),
                    "ask": Decimal("10.0000"),
                    "mid_price": Decimal("9.9999"),
                    "source": "LSEG",
                    "source_identifier": "EUR=",
                }
            ],
            "count": 1,
        }

    def get_market_bars(self, **kwargs: Any) -> dict[str, Any]:
        self.bar_calls += 1
        frequency = kwargs["frequency"]
        if frequency == "daily":
            step = timedelta(days=1)
            count = 60
        else:
            step = timedelta(hours=1)
            count = 240

        start = self.as_of - step * (count - 1)
        bars: list[dict[str, Any]] = []
        for index in range(count):
            timestamp = start + step * index
            close = Decimal("1.0500") + Decimal(index) / Decimal("10000")
            bars.append(
                {
                    "bar_date": timestamp.date(),
                    "bar_time": timestamp,
                    "frequency": frequency,
                    "open": close - Decimal("0.0002"),
                    "high": close + Decimal("0.0005"),
                    "low": close - Decimal("0.0005"),
                    "close": close,
                    "volume": Decimal("100"),
                    "source": "LSEG",
                    "source_identifier": "EUR=",
                }
            )

        bars.append(
            {
                **bars[-1],
                "bar_time": self.as_of + step,
                "bar_date": (self.as_of + step).date(),
                "close": Decimal("8.8888"),
            }
        )
        return {
            "instrument": {
                "instrument_id": 7,
                "canonical_symbol": "EURUSD",
            },
            "bars": list(reversed(bars)),
            "count": len(bars),
        }


def test_market_tool_filters_future_rows_computes_metrics_and_registers_ids(
    tmp_path,
) -> None:
    """行情 Tool 应生成可追溯指标，且绝不使用 as_of 之后的数据。"""
    context = _context()
    store = FxEvidenceStore(tmp_path, context.evidence_context_id)
    tool = GetFxMarketEvidenceTool(
        reader=_FakeReader(context.as_of),
        context=context,
        store=store,
    )

    output = json.loads(tool.execute(evidence_context_id=context.evidence_context_id))

    assert output["ok"] is True
    assert output["status"] == "complete"
    assert output["evidence_context_id"] == context.evidence_context_id
    assert output["query_id"].startswith("fxq-")
    assert datetime.fromisoformat(output["data_as_of"]) <= context.as_of
    assert any("available_time" in warning for warning in output["warnings"])

    evidence = output["evidence"]
    daily_latest = next(
        item
        for item in evidence
        if item["timeframe"] == "1D" and item["name"] == "latest_close"
    )
    assert daily_latest["value"] == pytest.approx(1.0559)
    assert daily_latest["value"] != 8.8888
    assert {
        "return_5",
        "return_20",
        "ema_20",
        "ema_50",
        "rsi_14",
        "atr_14",
        "realized_vol_20",
        "high_20",
        "low_20",
    }.issubset({item["name"] for item in evidence})

    lookup = GetFxEvidenceByIdsTool(context=context, store=store)
    selected_ids = [daily_latest["evidence_id"], evidence[-1]["evidence_id"]]
    fetched = json.loads(
        lookup.execute(
            evidence_context_id=context.evidence_context_id,
            evidence_ids=selected_ids + ["missing-id"],
        )
    )

    assert fetched["ok"] is True
    assert [item["evidence_id"] for item in fetched["evidence"]] == selected_ids
    assert fetched["not_found_ids"] == ["missing-id"]


def test_market_tool_rejects_cross_context_access(tmp_path) -> None:
    """Agent 不能借 Tool 参数读取另一个 Debate 的证据。"""
    context = _context()
    tool = GetFxMarketEvidenceTool(
        reader=_FakeReader(context.as_of),
        context=context,
        store=FxEvidenceStore(tmp_path, context.evidence_context_id),
    )

    output = json.loads(tool.execute(evidence_context_id="another-context"))

    assert output["ok"] is False
    assert output["status"] == "error"
    assert output["errors"][0]["code"] == "FX_EVIDENCE_ERROR"
    assert "Evidence Context" in output["errors"][0]["message"]


def test_aggregate_four_hour_skips_incomplete_bucket() -> None:
    start = datetime(2025, 7, 23, 0, tzinfo=timezone.utc)
    rows = [
        {
            "bar_time": start + timedelta(hours=offset),
            "open": 1.1,
            "high": 1.2,
            "low": 1.0,
            "close": 1.1,
            "volume": 1,
        }
        for offset in (0, 1, 3)
    ]

    assert aggregate_four_hour(rows) == []


def test_identical_context_query_is_cached_for_parallel_agents(tmp_path) -> None:
    """Bull/Bear/MT 的相同查询应共用一份冻结结果和 query_id。"""
    context = _context()
    reader = _FakeReader(context.as_of)
    tool = GetFxMarketEvidenceTool(
        reader=reader,
        context=context,
        store=FxEvidenceStore(tmp_path, context.evidence_context_id),
    )

    first = json.loads(tool.execute(evidence_context_id=context.evidence_context_id))
    second = json.loads(tool.execute(evidence_context_id=context.evidence_context_id))

    assert first == second
    assert reader.latest_calls == 1
    assert reader.bar_calls == 2
