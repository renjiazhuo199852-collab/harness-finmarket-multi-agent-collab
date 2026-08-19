"""Natural-language routing for the current FX Debate execution seam.

The router is deliberately side-effect free.  It only turns a user prompt
into a three-variable :class:`FxPairDebateRequest` or a deterministic
clarification.  Data-source availability is checked by the execution tool,
not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from src.fx_debate.request_adapter import FxPairDebateRequest
from src.tools.fx_deterministic_parser import (
    classify_fx_intent,
    extract_analysis_timeframes,
    extract_decision_horizon,
    extract_fx_pair_candidates,
    normalize_fx_text,
)
from src.tools.fx_nl_parser_contract import FxParsedIntent

FX_DEBATE_PRESET = "fx_debate_team"
FX_DEBATE_PRESET_ALIASES = frozenset(
    {
        FX_DEBATE_PRESET,
        "fx_pair_debate_desk_3vars_v1",
    }
)
DEFAULT_HORIZON = "P2W"
DEFAULT_TIMEFRAMES = ("PT4H", "P1D")
SUPPORTED_TIMEFRAMES = frozenset(DEFAULT_TIMEFRAMES)

_FX_CONTEXT_MARKERS = (
    "外汇",
    "汇率",
    "货币对",
    "FOREX",
    "FX",
    "CURRENCY PAIR",
    "多空辩论",
)
_DEBATE_INTENTS = frozenset({FxParsedIntent.directional, FxParsedIntent.debate})
_NON_DEBATE_INTENTS = frozenset(
    {
        FxParsedIntent.quote,
        FxParsedIntent.conversion,
        FxParsedIntent.market_data,
        FxParsedIntent.explanation,
        FxParsedIntent.summary,
        FxParsedIntent.backtest,
        FxParsedIntent.hedge,
        FxParsedIntent.live_execution,
        FxParsedIntent.unknown,
    }
)


@dataclass(frozen=True)
class FxRouteDecision:
    """Small route interface consumed by ``SwarmTool`` and tests."""

    route: Literal["fx_debate", "generic", "clarify"]
    request: FxPairDebateRequest | None = None
    reason_code: str | None = None
    message: str | None = None


class FxRouter:
    """Resolve deterministic FX Debate intent without starting execution."""

    def route(
        self,
        prompt: str,
        *,
        explicit_preset: str | None = None,
    ) -> FxRouteDecision:
        if not isinstance(prompt, str) or not prompt.strip():
            return self._clarify("FX_PROMPT_EMPTY", "请提供包含货币对和研究目标的问题。")

        forced_fx = _is_fx_preset(explicit_preset)
        if explicit_preset and not forced_fx:
            return FxRouteDecision("generic")

        text = normalize_fx_text(prompt)
        pair = extract_fx_pair_candidates(text)
        intent = classify_fx_intent(text)
        is_fx_context = bool(pair.canonical_symbols or pair.currency_mentions) or any(
            marker in text for marker in _FX_CONTEXT_MARKERS
        )

        if not forced_fx and not is_fx_context:
            return FxRouteDecision("generic")
        if not forced_fx and intent in _NON_DEBATE_INTENTS:
            return FxRouteDecision("generic")
        if not forced_fx and intent not in _DEBATE_INTENTS:
            return FxRouteDecision("generic")

        if pair.ambiguity_flags:
            if "cny_cnh_ambiguous" in pair.ambiguity_flags:
                return self._clarify(
                    "FX_CNY_CNH_AMBIGUOUS",
                    "请明确使用 USDCNY（在岸人民币）还是 USDCNH（离岸人民币）。",
                )
            if "multiple_fx_pairs" in pair.ambiguity_flags:
                return self._clarify(
                    "FX_MULTIPLE_PAIRS",
                    f"检测到多个货币对（{', '.join(pair.canonical_symbols)}），请只指定一个。",
                )
            return self._clarify(
                "FX_PAIR_UNSUPPORTED",
                "货币对格式或候选不受当前 FX Debate 支持，请提供明确的六字母货币对。",
            )
        if len(pair.canonical_symbols) != 1:
            return self._clarify(
                "FX_PAIR_MISSING",
                "请明确要分析的货币对，例如 EURUSD 或 EUR/USD。",
            )

        horizon = extract_decision_horizon(text)
        if horizon.ambiguity_flags:
            return self._clarify("FX_HORIZON_CONFLICT", "检测到互相冲突的研究期限，请只指定一个期限。")
        horizon_value = horizon.value or DEFAULT_HORIZON
        if horizon_value.endswith(("M", "Y")):
            return self._clarify(
                "FX_HORIZON_UNSUPPORTED",
                "当前 FX Debate 只支持不超过 90 天的日/周研究期限，请改用几天或几周。",
            )

        timeframes = extract_analysis_timeframes(text)
        if timeframes.ambiguity_flags:
            return self._clarify(
                "FX_TIMEFRAME_CONFLICT",
                "检测到互相冲突的分析周期，请使用 4H、1D 或 4H/1D。",
            )
        timeframe_values = timeframes.values or DEFAULT_TIMEFRAMES
        unsupported = [item for item in timeframe_values if item not in SUPPORTED_TIMEFRAMES]
        if unsupported:
            return self._clarify(
                "FX_TIMEFRAME_UNSUPPORTED",
                "当前 FX Debate 只支持 4H 和 1D 分析周期，请重新指定。",
            )

        timeframe = (
            f"decision_horizon={horizon_value}; "
            f"analysis_timeframes={','.join(timeframe_values)}"
        )
        try:
            request = FxPairDebateRequest(
                target=pair.canonical_symbols[0],
                timeframe=timeframe,
                goal=prompt.strip(),
            )
        except (ValidationError, ValueError) as exc:
            return self._clarify("FX_REQUEST_INVALID", str(exc))
        return FxRouteDecision("fx_debate", request=request)

    @staticmethod
    def _clarify(reason_code: str, message: str) -> FxRouteDecision:
        return FxRouteDecision(
            route="clarify",
            reason_code=reason_code,
            message=message,
        )


def route_fx_prompt(
    prompt: str,
    *,
    explicit_preset: str | None = None,
) -> FxRouteDecision:
    """Convenience function for callers that do not need a router instance."""

    return FxRouter().route(prompt, explicit_preset=explicit_preset)


def _is_fx_preset(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in FX_DEBATE_PRESET_ALIASES
