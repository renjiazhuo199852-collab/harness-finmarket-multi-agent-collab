"""Current-attempt authorization policy for Swarm-capable tools."""

from __future__ import annotations

import pytest

import src.agent.swarm_authorization as authorization_module
from src.agent.swarm_authorization import build_swarm_authorization


@pytest.mark.parametrize(
    "prompt",
    [
        "请让团队分析 EURUSD 未来两周走势。",
        "请用多智能体分析 EURUSD。",
        "请让多 Agent 分析 EURUSD。",
        "请让多个 Agent 分析 EURUSD。",
        "请进行多空辩论。",
        "请协作分析苹果公司财报。",
        "Please use FX Debate for EURUSD.",
        "让多个智能体分析 EURUSD。",
    ],
)
def test_explicit_team_language_authorizes_current_attempt(prompt: str) -> None:
    authorization = build_swarm_authorization(prompt)

    assert authorization.authorized is True
    assert authorization.raw_user_content == prompt


@pytest.mark.parametrize(
    "prompt",
    [
        "不要启动团队，分析 EURUSD 未来两周走势。",
        "不使用多智能体，分析 EURUSD。",
        "无需辩论，请给出 EURUSD 观点。",
        "禁止调用 Swarm 分析 EURUSD。",
        "别让多个 Agent 分析 EURUSD。",
    ],
)
def test_negative_language_has_priority_over_positive_keywords(prompt: str) -> None:
    authorization = build_swarm_authorization(prompt)

    assert authorization.authorized is False
    assert authorization.fx_decision is None


def test_plain_fx_request_is_not_authorized() -> None:
    authorization = build_swarm_authorization("分析 EURUSD 未来两周走势。")

    assert authorization.authorized is False
    assert authorization.fx_decision is None


def test_authorized_fx_request_uses_original_user_content_for_route() -> None:
    prompt = "请让团队分析 EURUSD 未来两周走势。"

    authorization = build_swarm_authorization(prompt)

    assert authorization.fx_decision is not None
    assert authorization.fx_decision.route == "fx_debate"
    assert authorization.fx_decision.request is not None
    assert authorization.fx_decision.request.goal == prompt


def test_authorized_non_fx_request_remains_generic() -> None:
    authorization = build_swarm_authorization("请让团队协作分析苹果公司财报。")

    assert authorization.authorized is True
    assert authorization.fx_decision is not None
    assert authorization.fx_decision.route == "generic"


def test_unauthorized_attempt_does_not_call_fx_router(monkeypatch) -> None:
    def fail_route(*args, **kwargs):
        del args, kwargs
        raise AssertionError("route_fx_prompt must not run before authorization")

    monkeypatch.setattr(authorization_module, "route_fx_prompt", fail_route)

    authorization = build_swarm_authorization("分析 EURUSD 未来两周走势。")

    assert authorization.authorized is False
    assert authorization.fx_decision is None
