"""Three-variable FX Debate request adapter contract."""

from __future__ import annotations

import pytest

from src.fx_debate.request_adapter import (
    FxSymbolCandidate,
    FxPairDebateRequest,
    ResolvedFxPair,
    adapt_fx_pair_debate_request,
)


@pytest.mark.parametrize("target", ["EURUSD", "EUR/USD", "eur-usd", "EUR USD"])
def test_adapter_normalizes_common_fx_pair_formats(target: str) -> None:
    adapted = adapt_fx_pair_debate_request(
        FxPairDebateRequest(
            target=target,
            timeframe="2 weeks; 4H/1D",
            goal="分析 EURUSD 未来两周走势。",
        )
    )

    assert adapted.resolved_request.canonical_symbol == "EURUSD"
    assert adapted.resolved_request.display_symbol == "EUR/USD"
    assert adapted.resolved_request.base_currency == "EUR"
    assert adapted.resolved_request.quote_currency == "USD"
    assert adapted.resolved_request.horizon == "2 weeks"
    assert adapted.resolved_request.timeframe == "4H/1D"


def test_adapter_accepts_explicit_timeframe_labels() -> None:
    adapted = adapt_fx_pair_debate_request(
        FxPairDebateRequest(
            target="EUR/USD",
            timeframe="horizon=14 days; bars=4H",
            goal="仅研究宏观和技术面。",
        )
    )

    assert adapted.resolved_request.horizon == "14 days"
    assert adapted.resolved_request.timeframe == "4H"


def test_adapter_accepts_upstream_iso_timeframe_contract() -> None:
    adapted = adapt_fx_pair_debate_request(
        FxPairDebateRequest(
            target="EURUSD",
            timeframe="decision_horizon=P2W; analysis_timeframes=PT4H,P1D",
            goal="兼容上游 deterministic 3vars 输出。",
        )
    )

    assert adapted.resolved_request.horizon == "2 weeks"
    assert adapted.resolved_request.timeframe == "4H/1D"


def test_adapter_rejects_iso_month_horizon() -> None:
    with pytest.raises(ValueError, match="days or weeks|1-90 days"):
        adapt_fx_pair_debate_request(
            FxPairDebateRequest(
                target="EURUSD",
                timeframe="decision_horizon=P1M; analysis_timeframes=PT4H,P1D",
                goal="月份期限不属于当前 MVP。",
            )
        )


@pytest.mark.parametrize(
    "timeframe",
    ["short term", "2 weeks", "2 weeks; 1H", "91 days; 4H/1D"],
)
def test_adapter_rejects_ambiguous_or_unsupported_timeframe(timeframe: str) -> None:
    with pytest.raises(ValueError, match="timeframe"):
        adapt_fx_pair_debate_request(
            FxPairDebateRequest(
                target="EURUSD",
                timeframe=timeframe,
                goal="测试。",
            )
        )


def test_adapter_keeps_non_mvp_pair_resolvable_for_future_database_coverage() -> None:
    adapted = adapt_fx_pair_debate_request(
        FxPairDebateRequest(
            target="GBP/USD",
            timeframe="1 week; 1D",
            goal="研究 GBPUSD。",
        )
    )

    assert adapted.resolved_request.canonical_symbol == "GBPUSD"
    assert adapted.resolved_request.pair_class == "major"


def test_adapter_accepts_one_future_database_candidate() -> None:
    class _OneCandidateResolver:
        def resolve(self, target: str):
            assert target == "eur usd"
            return [
                FxSymbolCandidate(
                    pair=ResolvedFxPair(
                        canonical_symbol="EURUSD",
                        display_symbol="EUR/USD",
                        base_currency="EUR",
                        quote_currency="USD",
                        requested_base_currency="EUR",
                        requested_quote_currency="USD",
                        inverted=False,
                        pair_class="major",
                    ),
                    score=0.94,
                    matched_by="database_alias",
                )
            ]

    adapted = adapt_fx_pair_debate_request(
        FxPairDebateRequest(
            target="eur usd",
            timeframe="2 weeks; 4H/1D",
            goal="测试数据库别名解析。",
        ),
        resolver=_OneCandidateResolver(),
    )

    assert adapted.resolved_request.canonical_symbol == "EURUSD"


def test_adapter_rejects_ambiguous_future_database_candidates() -> None:
    eurusd = ResolvedFxPair(
        canonical_symbol="EURUSD",
        display_symbol="EUR/USD",
        base_currency="EUR",
        quote_currency="USD",
        requested_base_currency="EUR",
        requested_quote_currency="USD",
        inverted=False,
        pair_class="major",
    )
    eurchf = ResolvedFxPair(
        canonical_symbol="EURCHF",
        display_symbol="EUR/CHF",
        base_currency="EUR",
        quote_currency="CHF",
        requested_base_currency="EUR",
        requested_quote_currency="CHF",
        inverted=False,
        pair_class="minor",
    )

    class _AmbiguousResolver:
        def resolve(self, target: str):
            return [FxSymbolCandidate(eurusd, 0.8), FxSymbolCandidate(eurchf, 0.79)]

    with pytest.raises(ValueError, match="多个货币对"):
        adapt_fx_pair_debate_request(
            FxPairDebateRequest(
                target="eur", timeframe="2 weeks; 4H/1D", goal="测试。"
            ),
            resolver=_AmbiguousResolver(),
        )
