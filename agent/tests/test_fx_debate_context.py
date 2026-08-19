"""FX Debate request validation and Evidence Context construction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.fx_debate.context import build_evidence_context
from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions


def _eurusd_request(**overrides: object) -> ResolvedFxDebateRequest:
    payload = {
        "status": "resolved",
        "asset_class": "fx",
        "instrument_type": "spot",
        "pair_class": "major",
        "canonical_symbol": "EURUSD",
        "display_symbol": "EUR/USD",
        "base_currency": "EUR",
        "quote_currency": "USD",
        "requested_base_currency": "EUR",
        "requested_quote_currency": "USD",
        "inverted": False,
        "horizon": "2 weeks",
        "timeframe": "4H/1D",
    }
    payload.update(overrides)
    return ResolvedFxDebateRequest.model_validate(payload)


def test_resolved_eurusd_request_builds_one_read_only_evidence_context() -> None:
    context = build_evidence_context(
        _eurusd_request(),
        RunOptions(
            request_id="req-test-001",
            as_of=datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
        ),
        evidence_context_id="fxctx-test-001",
    )

    assert context.evidence_context_id == "fxctx-test-001"
    assert context.request_id == "req-test-001"
    assert context.canonical_symbol == "EURUSD"
    assert context.horizon_days == 14
    assert context.timeframes == ["4H", "1D"]
    assert context.market_start_time.isoformat() == "2025-06-19T02:00:00+00:00"
    assert context.news_start_time.isoformat() == "2026-07-10T02:00:00+00:00"
    assert context.market_bar_limit_per_timeframe == {"4H": 250, "1D": 260}
    assert context.macro_observation_limit == 24
    assert context.news_limit == 20
    assert context.provider_priority == ["LSEG"]


def test_resolved_request_rejects_direction_fields_that_disagree() -> None:
    with pytest.raises(ValidationError, match="inverted"):
        _eurusd_request(
            requested_base_currency="USD",
            requested_quote_currency="EUR",
            inverted=False,
        )


def test_evidence_context_rejects_unsupported_timeframes() -> None:
    with pytest.raises(ValueError, match="timeframe"):
        build_evidence_context(
            _eurusd_request(timeframe="1H/1D"),
            RunOptions(as_of=datetime(2026, 7, 24, tzinfo=timezone.utc)),
        )
