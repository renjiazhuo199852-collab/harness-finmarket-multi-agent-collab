"""Pure contracts for deterministic FX natural-language parsing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ParseSource(str, Enum):
    explicit = "explicit"
    normalized = "normalized"
    inferred = "inferred"
    ambiguous = "ambiguous"
    missing = "missing"


class FxParsedIntent(str, Enum):
    quote = "quote"
    conversion = "conversion"
    market_data = "market_data"
    explanation = "explanation"
    summary = "summary"
    directional = "directional"
    debate = "debate"
    hedge = "hedge"
    backtest = "backtest"
    live_execution = "live_execution"
    unknown = "unknown"


@dataclass(frozen=True)
class FxNaturalLanguageParse:
    """Parser output before route and execution validation."""

    intent_candidate: FxParsedIntent
    symbol_candidates: tuple[str, ...] = ()
    decision_horizon_candidate: str | None = None
    analysis_timeframes_candidate: tuple[str, ...] = ()
    symbol_source: ParseSource = ParseSource.missing
    horizon_source: ParseSource = ParseSource.missing
    timeframes_source: ParseSource = ParseSource.missing
    ambiguity_flags: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class FxParserResult:
    """Secret-free result envelope for the deterministic parser."""

    status: str
    parsed: FxNaturalLanguageParse | None
    error_code: str | None = None
