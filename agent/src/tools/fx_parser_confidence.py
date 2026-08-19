"""Versioned deterministic confidence policy for FX routing."""

from __future__ import annotations

from types import MappingProxyType

from src.tools.fx_nl_parser_contract import ParseSource

FX_CONFIDENCE_POLICY_VERSION = "fx-confidence-v1"

PARSE_SOURCE_SCORE = MappingProxyType(
    {
        ParseSource.explicit: 1.0,
        ParseSource.normalized: 0.95,
        ParseSource.inferred: 0.7,
        ParseSource.ambiguous: 0.35,
        ParseSource.missing: 0.0,
    }
)
