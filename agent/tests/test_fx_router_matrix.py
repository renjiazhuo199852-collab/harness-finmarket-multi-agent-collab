"""Acceptance matrix for deterministic FX route classification.

These cases exercise the public ``route_fx_prompt`` seam.  They deliberately
do not start a Swarm, call an LLM, or access market data: route correctness
must remain deterministic and testable when those dependencies are offline.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.fx_debate.router import route_fx_prompt


@dataclass(frozen=True)
class RouteCase:
    case_id: str
    prompt: str
    expected_route: str
    expected_target: str | None = None
    expected_timeframe: str | None = None
    expected_reason: str | None = None
    explicit_preset: str | None = None


DEFAULT_TIMEFRAME = "decision_horizon=P2W; analysis_timeframes=PT4H,P1D"


NORMAL_CASES = (
    RouteCase(
        "N01",
        "分析 EURUSD 未来两周走势并给出交易建议",
        "fx_debate",
        "EURUSD",
        DEFAULT_TIMEFRAME,
    ),
    RouteCase(
        "N02",
        "analyze eur/usd next two weeks outlook and trade idea",
        "fx_debate",
        "EURUSD",
        DEFAULT_TIMEFRAME,
    ),
    RouteCase(
        "N03",
        "分析欧元兑美元接下来两周的多空机会",
        "fx_debate",
        "EURUSD",
        DEFAULT_TIMEFRAME,
    ),
    RouteCase(
        "N04",
        "分析 GBP-USD 未来 10 天走势并给出交易建议",
        "fx_debate",
        "GBPUSD",
        "decision_horizon=P10D; analysis_timeframes=PT4H,P1D",
    ),
    RouteCase(
        "N05",
        "分析 EURUSD，decision_horizon=P2W; analysis_timeframes=PT4H,P1D",
        "fx_debate",
        "EURUSD",
        DEFAULT_TIMEFRAME,
    ),
    RouteCase(
        "N06",
        "请用五 Agent Debate 分析 EURUSD",
        "fx_debate",
        "EURUSD",
        DEFAULT_TIMEFRAME,
    ),
)


BOUNDARY_CASES = (
    RouteCase(
        "B01",
        "分析 EURUSD 和 GBPUSD 未来两周走势",
        "clarify",
        expected_reason="FX_MULTIPLE_PAIRS",
    ),
    RouteCase(
        "B02",
        "分析 USD/人民币未来两周走势",
        "clarify",
        expected_reason="FX_CNY_CNH_AMBIGUOUS",
    ),
    RouteCase(
        "B03",
        "分析 EURUSD 未来两周，使用 1H 和 1D",
        "clarify",
        expected_reason="FX_TIMEFRAME_UNSUPPORTED",
    ),
    RouteCase(
        "B04",
        "分析 EURUSD 未来两周，同时看 4H 和 1H",
        "clarify",
        expected_reason="FX_TIMEFRAME_UNSUPPORTED",
    ),
    RouteCase(
        "B05",
        "分析 EURUSD 未来一个月走势",
        "clarify",
        expected_reason="FX_HORIZON_UNSUPPORTED",
    ),
    RouteCase(
        "B06",
        "分析 EURUSD 未来两周和未来一个月走势",
        "clarify",
        expected_reason="FX_HORIZON_CONFLICT",
    ),
    RouteCase(
        "B07",
        "分析 EURUSD 报价并预测未来两周走势",
        "generic",
    ),
    RouteCase(
        "B08",
        "",
        "clarify",
        expected_reason="FX_PROMPT_EMPTY",
    ),
)


GENERIC_CASES = (
    RouteCase("G01", "查询 EURUSD 当前汇率", "generic"),
    RouteCase("G02", "把 1000 欧元兑换成美元", "generic"),
    RouteCase("G03", "分析 AAPL 未来两周走势", "generic"),
    RouteCase("G04", "判断 BTCUSDT 未来两周走势", "generic"),
    RouteCase("G05", "分析 ETHUSD 未来两周趋势", "generic"),
    RouteCase("G06", "解释美联储加息对美元的影响", "generic"),
    RouteCase("G07", "分析未来两周走势并给出交易建议", "generic"),
)


@pytest.mark.parametrize("case", NORMAL_CASES, ids=lambda case: case.case_id)
def test_normal_fx_cases_route_to_current_debate(case: RouteCase) -> None:
    decision = route_fx_prompt(case.prompt, explicit_preset=case.explicit_preset)

    assert decision.route == case.expected_route
    assert decision.request is not None
    assert decision.request.target == case.expected_target
    assert decision.request.timeframe == case.expected_timeframe
    assert decision.request.goal == case.prompt
    assert decision.reason_code is None


@pytest.mark.parametrize("case", BOUNDARY_CASES, ids=lambda case: case.case_id)
def test_boundary_cases_fail_closed_or_remain_generic(case: RouteCase) -> None:
    decision = route_fx_prompt(case.prompt, explicit_preset=case.explicit_preset)

    assert decision.route == case.expected_route
    assert decision.reason_code == case.expected_reason
    if case.expected_route == "clarify":
        assert decision.request is None
        assert decision.message


@pytest.mark.parametrize("case", GENERIC_CASES, ids=lambda case: case.case_id)
def test_non_debate_cases_do_not_enter_fx_debate(case: RouteCase) -> None:
    decision = route_fx_prompt(case.prompt)

    assert decision.route == case.expected_route
    assert decision.request is None
    assert decision.reason_code is None


@pytest.mark.parametrize(
    "preset",
    ["fx_debate_team", "fx_pair_debate_desk_3vars_v1"],
    ids=["canonical", "legacy-alias"],
)
def test_explicit_fx_presets_keep_the_compatibility_alias(preset: str) -> None:
    decision = route_fx_prompt("分析 EURUSD", explicit_preset=preset)

    assert decision.route == "fx_debate"
    assert decision.request is not None
    assert decision.request.target == "EURUSD"
    assert decision.request.timeframe == DEFAULT_TIMEFRAME
