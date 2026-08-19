"""Public three-variable request adaptation for the FX Debate desk."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions

_PAIR_PATTERN = re.compile(r"^[A-Z]{6}$")
_TIMEFRAME_PATTERN = re.compile(
    r"^(?:horizon\s*=\s*)?(?P<horizon>\d+\s+(?:day|days|week|weeks))\s*;\s*"
    r"(?:bars\s*=\s*)?(?P<bars>4h(?:\s*/\s*1d)?|1d)$",
    re.IGNORECASE,
)
_ISO_TIMEFRAME_PATTERN = re.compile(
    r"^decision_horizon\s*=\s*(?P<horizon>P[1-9]\d*[DWMY])\s*;\s*"
    r"analysis_timeframes\s*=\s*(?P<bars>(?:PT[1-9]\d*[HM]|P[1-9]\d*[DWMY])"
    r"(?:\s*,\s*(?:PT[1-9]\d*[HM]|P[1-9]\d*[DWMY]))*)$",
    re.IGNORECASE,
)
_HORIZON_PATTERN = re.compile(
    r"^(?P<count>\d+)\s+(?P<unit>day|days|week|weeks)$", re.IGNORECASE
)
_MAJOR_PAIRS = frozenset(
    {
        "AUDUSD",
        "EURUSD",
        "GBPUSD",
        "NZDUSD",
        "USDCAD",
        "USDCHF",
        "USDJPY",
    }
)


class FxPairDebateRequest(BaseModel):
    """Stable upstream request contract for an FX pair research debate."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=80)
    timeframe: str = Field(min_length=1, max_length=80)
    goal: str = Field(min_length=1, max_length=4_000)


@dataclass(frozen=True)
class ResolvedFxPair:
    """Resolver-owned normalized symbol identity, independent of data storage."""

    canonical_symbol: str
    display_symbol: str
    base_currency: str
    quote_currency: str
    requested_base_currency: str
    requested_quote_currency: str
    inverted: bool
    pair_class: Literal["major", "minor", "exotic"]


@dataclass(frozen=True)
class FxSymbolCandidate:
    """A possible resolver match for a future fuzzy database lookup."""

    pair: ResolvedFxPair
    score: float = 1.0
    matched_by: str = "exact"


class FxSymbolResolver(Protocol):
    """Boundary for future database-backed aliases and fuzzy-match candidates."""

    def resolve(
        self, target: str
    ) -> (
        ResolvedFxPair
        | FxSymbolCandidate
        | Sequence[ResolvedFxPair | FxSymbolCandidate]
    ):
        """Resolve a target, or return candidates for a fuzzy match."""


class DeterministicFxSymbolResolver:
    """Normalize standard six-letter pairs until a database resolver is wired."""

    def resolve(self, target: str) -> ResolvedFxPair:
        normalized = re.sub(r"[\s/_-]+", "", target or "").upper()
        if not _PAIR_PATTERN.fullmatch(normalized):
            raise ValueError(
                "target must resolve to a six-letter FX pair, for example EURUSD or EUR/USD"
            )
        base, quote = normalized[:3], normalized[3:]
        return ResolvedFxPair(
            canonical_symbol=normalized,
            display_symbol=f"{base}/{quote}",
            base_currency=base,
            quote_currency=quote,
            requested_base_currency=base,
            requested_quote_currency=quote,
            inverted=False,
            pair_class="major" if normalized in _MAJOR_PAIRS else "minor",
        )


@dataclass(frozen=True)
class AdaptedFxPairDebateRequest:
    """Private request data derived from the three public upstream variables."""

    public_request: FxPairDebateRequest
    resolved_request: ResolvedFxDebateRequest
    run_options: RunOptions


def adapt_fx_pair_debate_request(
    request: FxPairDebateRequest,
    *,
    resolver: FxSymbolResolver | None = None,
    run_options: RunOptions | None = None,
) -> AdaptedFxPairDebateRequest:
    """Validate public variables and create the private FX execution request."""
    raw_resolution = (resolver or DeterministicFxSymbolResolver()).resolve(
        request.target
    )
    pair = _select_unambiguous_pair(raw_resolution, request.target)
    horizon, bars = _parse_timeframe(request.timeframe)
    options = run_options or RunOptions()
    return AdaptedFxPairDebateRequest(
        public_request=request,
        resolved_request=ResolvedFxDebateRequest(
            status="resolved",
            asset_class="fx",
            instrument_type="spot",
            pair_class=pair.pair_class,
            canonical_symbol=pair.canonical_symbol,
            display_symbol=pair.display_symbol,
            base_currency=pair.base_currency,
            quote_currency=pair.quote_currency,
            requested_base_currency=pair.requested_base_currency,
            requested_quote_currency=pair.requested_quote_currency,
            inverted=pair.inverted,
            horizon=horizon,
            timeframe=bars,
            goal=request.goal,
        ),
        run_options=options,
    )


def _select_unambiguous_pair(value: Any, target: str) -> ResolvedFxPair:
    """Normalize current and future resolver return shapes at one boundary."""
    if isinstance(value, FxSymbolCandidate):
        return value.pair
    if isinstance(value, ResolvedFxPair):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        candidates = [
            item.pair if isinstance(item, FxSymbolCandidate) else item for item in value
        ]
        if not candidates:
            raise ValueError(f"target {target!r} 未找到匹配的外汇货币对")
        if len(candidates) > 1:
            labels = ", ".join(
                item.display_symbol
                for item in candidates[:5]
                if isinstance(item, ResolvedFxPair)
            )
            raise ValueError(
                f"target {target!r} 匹配到多个货币对（{labels}），请提供更明确的格式"
            )
        if isinstance(candidates[0], ResolvedFxPair):
            return candidates[0]
    raise TypeError("FX symbol resolver must return a pair or a candidate sequence")


def _parse_timeframe(value: str) -> tuple[str, str]:
    iso_match = _ISO_TIMEFRAME_PATTERN.fullmatch(value.strip())
    if iso_match is not None:
        return _parse_iso_timeframe(iso_match.group("horizon"), iso_match.group("bars"))

    match = _TIMEFRAME_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(
            "timeframe must use '<n> days|weeks; 4H|1D|4H/1D' or "
            "'decision_horizon=P2W; analysis_timeframes=PT4H,P1D'"
        )
    horizon = " ".join(match.group("horizon").lower().split())
    horizon_match = _HORIZON_PATTERN.fullmatch(horizon)
    assert horizon_match is not None
    days = int(horizon_match.group("count")) * (
        7 if horizon_match.group("unit").startswith("week") else 1
    )
    if not 1 <= days <= 90:
        raise ValueError("timeframe horizon must resolve to 1-90 days")
    bars = "/".join(part.strip().upper() for part in match.group("bars").split("/"))
    return horizon, bars


def _parse_iso_timeframe(horizon_value: str, bars_value: str) -> tuple[str, str]:
    """Normalize the upstream ISO contract into the current internal fields."""
    horizon_value = horizon_value.upper()
    bars = [item.strip().upper() for item in bars_value.split(",") if item.strip()]
    if not bars or any(item not in {"PT4H", "P1D"} for item in bars):
        raise ValueError("analysis_timeframes must contain PT4H and/or P1D")

    unique_bars: list[str] = []
    for item in bars:
        if item not in unique_bars:
            unique_bars.append(item)

    duration_match = re.fullmatch(r"P(?P<count>[1-9]\d*)(?P<unit>[DWMY])", horizon_value)
    if duration_match is None:
        raise ValueError("decision_horizon must be an ISO day/week duration")
    count = int(duration_match.group("count"))
    unit = duration_match.group("unit")
    if unit == "D":
        days = count
        horizon = f"{count} day" if count == 1 else f"{count} days"
    elif unit == "W":
        days = count * 7
        horizon = f"{count} week" if count == 1 else f"{count} weeks"
    else:
        raise ValueError("timeframe horizon must use days or weeks, not months or years")
    if not 1 <= days <= 90:
        raise ValueError("timeframe horizon must resolve to 1-90 days")

    bars = "/".join("4H" if item == "PT4H" else "1D" for item in unique_bars)
    return horizon, bars
