"""Small deterministic contracts shared by the FX natural-language parser.

This module intentionally contains no runtime, provider, database, or LLM
dependency.  The canonical allowlist is only a parsing guard; final symbol
resolution remains owned by ``src.fx_debate.request_adapter``.
"""

from __future__ import annotations

ALLOWED_FX_SYMBOLS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "USDCNY",
    "USDCNH",
)

FX_DETERMINISTIC_CONTRACT_VERSION = "fx-routing-contract-v1"
