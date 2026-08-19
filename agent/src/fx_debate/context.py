"""Deterministic construction of one read-only Evidence Context."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

from src.fx_debate.models import EvidenceContext, ResolvedFxDebateRequest, RunOptions

_HORIZON_PATTERN = re.compile(r"^(?P<count>\d+)\s+(?P<unit>day|days|week|weeks)$")
_SUPPORTED_TIMEFRAMES = ("4H", "1D")
_MARKET_WINDOW_DAYS = 400


def _horizon_days(value: str) -> int:
    match = _HORIZON_PATTERN.fullmatch(value.strip().lower())
    if match is None:
        raise ValueError("horizon must use '<n> days' or '<n> weeks'")
    count = int(match.group("count"))
    days = count * (7 if match.group("unit").startswith("week") else 1)
    if not 1 <= days <= 90:
        raise ValueError("horizon must resolve to 1-90 days")
    return days


def _timeframes(value: str) -> list[Literal["4H", "1D"]]:
    values = [item.strip().upper() for item in value.split("/") if item.strip()]
    if not values or any(item not in _SUPPORTED_TIMEFRAMES for item in values):
        raise ValueError("timeframe must contain only 4H and/or 1D")
    return cast(list[Literal["4H", "1D"]], list(dict.fromkeys(values)))


def build_evidence_context(
    request: ResolvedFxDebateRequest,
    options: RunOptions | None = None,
    *,
    evidence_context_id: str | None = None,
    now: datetime | None = None,
) -> EvidenceContext:
    """Build the immutable query policy shared by one Debate run.

    Args:
        request: Planner-resolved FX request.
        options: Optional run controls.
        evidence_context_id: Deterministic override used by tests and replay.
        now: Clock injection used only when ``options.as_of`` is absent.

    Returns:
        A validated Evidence Context.
    """
    options = options or RunOptions()
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("now must include a timezone")
    as_of = options.as_of or clock
    days = _horizon_days(request.horizon)
    timeframes = _timeframes(request.timeframe)
    context_id = evidence_context_id or f"fxctx-{uuid.uuid4().hex[:16]}"
    request_id = options.request_id or f"req-{uuid.uuid4().hex[:16]}"

    limits = {"4H": 250, "1D": 260}
    return EvidenceContext(
        evidence_context_id=context_id,
        request_id=request_id,
        canonical_symbol=request.canonical_symbol,
        display_symbol=request.display_symbol,
        base_currency=request.base_currency,
        quote_currency=request.quote_currency,
        requested_base_currency=request.requested_base_currency,
        requested_quote_currency=request.requested_quote_currency,
        inverted=request.inverted,
        pair_class=request.pair_class,
        horizon=request.horizon,
        goal=request.goal,
        horizon_days=days,
        timeframes=timeframes,
        as_of=as_of,
        risk_profile=options.risk_profile,
        provider_priority=["LSEG"],
        market_start_time=as_of - timedelta(days=_MARKET_WINDOW_DAYS),
        news_start_time=as_of - timedelta(days=max(days, 7)),
        market_bar_limit_per_timeframe={
            timeframe: limits[timeframe] for timeframe in timeframes
        },
        macro_observation_limit=24,
        news_limit=20,
    )
