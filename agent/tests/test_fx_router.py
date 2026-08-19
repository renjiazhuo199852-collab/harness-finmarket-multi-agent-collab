"""Deterministic natural-language routing into the canonical FX Debate tool."""

from __future__ import annotations

import json

import pytest

import src.tools.swarm_tool as swarm_tool
from src.fx_debate.router import route_fx_prompt


@pytest.mark.parametrize(
    "prompt",
    [
        "分析 EURUSD 未来两周走势，结合 4H 和 1D 给出交易建议",
        "分析 EUR/USD 未来两周走势，结合 4 小时和日线给出交易建议",
        "分析 EUR-USD 未来两周走势，给出多空判断",
        "分析欧元/美元未来两周走势，给出交易建议",
    ],
)
def test_fx_directional_prompts_resolve_to_three_variables(prompt: str) -> None:
    decision = route_fx_prompt(prompt)

    assert decision.route == "fx_debate"
    assert decision.request is not None
    assert decision.request.target == "EURUSD"
    assert decision.request.timeframe == (
        "decision_horizon=P2W; analysis_timeframes=PT4H,P1D"
    )
    assert decision.request.goal == prompt


def test_missing_timeframe_uses_deterministic_defaults() -> None:
    decision = route_fx_prompt("请用五 Agent Debate 分析 EURUSD")

    assert decision.route == "fx_debate"
    assert decision.request is not None
    assert decision.request.timeframe.endswith("PT4H,P1D")


def test_quote_request_does_not_start_fx_debate() -> None:
    decision = route_fx_prompt("查询 EURUSD 当前汇率")

    assert decision.route == "generic"
    assert decision.request is None


@pytest.mark.parametrize(
    ("prompt", "reason_code"),
    [
        ("分析 EURUSD 和 GBPUSD 未来两周走势", "FX_MULTIPLE_PAIRS"),
        ("分析 USD/人民币未来两周走势", "FX_CNY_CNH_AMBIGUOUS"),
        ("分析 EURUSD 未来一个月走势", "FX_HORIZON_UNSUPPORTED"),
        ("分析 EURUSD 未来两周，使用 1H 和 1D", "FX_TIMEFRAME_UNSUPPORTED"),
    ],
)
def test_ambiguous_or_unsupported_fx_requests_do_not_start(prompt: str, reason_code: str) -> None:
    decision = route_fx_prompt(prompt)

    assert decision.route == "clarify"
    assert decision.reason_code == reason_code
    assert decision.request is None


@pytest.mark.parametrize("prompt", [
    "分析 AAPL 未来两周走势",
    "判断 BTCUSDT 未来两周走势",
    "分析 ETHUSD 未来两周趋势",
])
def test_non_fx_directional_request_remains_generic(prompt: str) -> None:
    decision = route_fx_prompt(prompt)

    assert decision.route == "generic"


@pytest.mark.parametrize("preset", ["fx_pair_debate_desk_3vars_v1", "fx_debate_team"])
def test_legacy_and_canonical_fx_preset_names_force_fx_route(preset: str) -> None:
    decision = route_fx_prompt("分析 EURUSD", explicit_preset=preset)

    assert decision.route == "fx_debate"
    assert decision.request is not None


def test_swarm_tool_delegates_fx_before_generic_runtime(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_execute(decision, *, event_callback):
        captured["decision"] = decision
        captured["event_callback"] = event_callback
        return json.dumps({"status": "completed", "preset": "fx_debate_team"})

    def fail_generic(*_args, **_kwargs):
        raise AssertionError("generic SwarmRuntime must not be started for FX Debate")

    monkeypatch.setattr(swarm_tool, "_execute_fx_debate_request", fake_execute)
    monkeypatch.setattr(swarm_tool, "_resolve_preset", fail_generic)

    payload = json.loads(
        swarm_tool.SwarmTool().execute(prompt="分析 EURUSD 未来两周走势并给出交易建议")
    )

    assert payload["preset"] == "fx_debate_team"
    decision = captured["decision"]
    assert decision.route == "fx_debate"
    assert decision.request.target == "EURUSD"


def test_swarm_tool_fails_closed_when_fx_data_is_unavailable(monkeypatch) -> None:
    from src.tools.run_fx_debate_tool import RunFxDebateTool

    monkeypatch.setattr(
        RunFxDebateTool,
        "check_available",
        classmethod(lambda cls: False),
    )
    payload = json.loads(
        swarm_tool.SwarmTool().execute(prompt="分析 EURUSD 未来两周走势")
    )

    assert payload == {
        "status": "error",
        "route": "fx_debate",
        "code": "FX_DATA_UNAVAILABLE",
        "message": "FX 数据源未配置，无法启动 Debate。",
    }
