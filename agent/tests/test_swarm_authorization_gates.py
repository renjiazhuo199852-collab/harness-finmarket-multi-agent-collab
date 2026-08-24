"""Defense-in-depth gates for current-attempt Swarm authorization."""

from __future__ import annotations

import json

from src import tools as tools_package
from src.agent.swarm_authorization import build_swarm_authorization
from src.tools.run_fx_debate_tool import RunFxDebateTool
from src.tools.swarm_tool import SwarmTool


def _definition_names(registry) -> set[str]:
    return {
        item["function"]["name"]
        for item in registry.get_definitions()
    }


def test_unauthorized_registry_hides_both_swarm_entry_tools(monkeypatch) -> None:
    def fail_availability(cls) -> bool:
        del cls
        raise AssertionError("hidden team tools must be skipped before availability checks")

    monkeypatch.setattr(
        tools_package,
        "_discover_subclasses",
        lambda: [SwarmTool, RunFxDebateTool],
    )
    monkeypatch.setattr(SwarmTool, "check_available", classmethod(fail_availability))
    monkeypatch.setattr(RunFxDebateTool, "check_available", classmethod(fail_availability))

    registry = tools_package.build_registry(
        swarm_authorization=build_swarm_authorization(
            "分析 EURUSD 未来两周走势。"
        )
    )

    assert "run_swarm" not in registry.tool_names
    assert "run_fx_debate" not in registry.tool_names
    assert "run_swarm" not in _definition_names(registry)
    assert "run_fx_debate" not in _definition_names(registry)


def test_authorized_fx_registry_exposes_both_entry_tools(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_package,
        "_discover_subclasses",
        lambda: [SwarmTool, RunFxDebateTool],
    )
    monkeypatch.setattr(
        RunFxDebateTool,
        "check_available",
        classmethod(lambda cls: True),
    )

    registry = tools_package.build_registry(
        swarm_authorization=build_swarm_authorization(
            "请让团队分析 EURUSD 未来两周走势。"
        )
    )

    assert {"run_swarm", "run_fx_debate"}.issubset(registry.tool_names)


def test_authorized_non_fx_registry_exposes_only_generic_swarm(monkeypatch) -> None:
    def fail_fx_availability(cls) -> bool:
        del cls
        raise AssertionError("non-FX attempts must hide run_fx_debate")

    monkeypatch.setattr(
        tools_package,
        "_discover_subclasses",
        lambda: [SwarmTool, RunFxDebateTool],
    )
    monkeypatch.setattr(
        RunFxDebateTool,
        "check_available",
        classmethod(fail_fx_availability),
    )

    registry = tools_package.build_registry(
        swarm_authorization=build_swarm_authorization(
            "请让团队协作分析苹果公司财报。"
        )
    )

    assert "run_swarm" in registry.tool_names
    assert "run_fx_debate" not in registry.tool_names


def test_swarm_execute_rejects_forged_arguments_before_routing(monkeypatch) -> None:
    import src.tools.swarm_tool as swarm_tool_module

    def fail_route(*args, **kwargs):
        del args, kwargs
        raise AssertionError("unauthorized execution must stop before FX routing")

    monkeypatch.setattr(swarm_tool_module, "route_fx_prompt", fail_route)
    tool = SwarmTool(
        swarm_authorization=build_swarm_authorization(
            "分析 EURUSD 未来两周走势。"
        )
    )

    payload = json.loads(
        tool.execute(
            prompt="请让团队分析 EURUSD",
            preset_name="fx_debate_team",
            team_authorized=True,
        )
    )

    assert payload["code"] == "SWARM_NOT_AUTHORIZED"


def test_fx_execute_rejects_before_route_evidence_or_orchestrator(monkeypatch) -> None:
    import src.tools.run_fx_debate_tool as fx_tool_module

    class FailFactory:
        def build(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("evidence factory must not run")

    class FailOrchestrator:
        def run(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("orchestrator must not run")

    def fail_context(*args, **kwargs):
        del args, kwargs
        raise AssertionError("FX request adaptation/context must not run")

    monkeypatch.setattr(fx_tool_module, "build_evidence_context", fail_context)
    tool = RunFxDebateTool(
        orchestrator=FailOrchestrator(),
        evidence_factory=FailFactory(),
        swarm_authorization=build_swarm_authorization(
            "分析 EURUSD 未来两周走势。"
        ),
    )

    payload = json.loads(
        tool.execute(
            target="EURUSD",
            timeframe="2 weeks",
            goal="请让团队分析",
            preset_name="fx_debate_team",
            team_authorized=True,
        )
    )

    assert payload["error"]["code"] == "SWARM_NOT_AUTHORIZED"


def test_unauthorized_fx_tool_does_not_construct_default_execution_dependencies(
    monkeypatch,
) -> None:
    import src.tools.run_fx_debate_tool as fx_tool_module

    def fail_constructor(*args, **kwargs):
        del args, kwargs
        raise AssertionError("execution dependencies must be lazy behind authorization")

    monkeypatch.setattr(fx_tool_module, "DefaultFxDebateOrchestrator", fail_constructor)
    monkeypatch.setattr(fx_tool_module, "FxEvidenceFactory", fail_constructor)

    tool = RunFxDebateTool(
        swarm_authorization=build_swarm_authorization(
            "分析 EURUSD 未来两周走势。"
        )
    )
    payload = json.loads(
        tool.execute(
            target="EURUSD",
            timeframe="2 weeks",
            goal="请让团队分析",
        )
    )

    assert payload["error"]["code"] == "SWARM_NOT_AUTHORIZED"


def test_authorized_fx_swarm_uses_raw_attempt_route_not_tool_arguments(monkeypatch) -> None:
    import src.tools.swarm_tool as swarm_tool_module

    authorization = build_swarm_authorization(
        "请让团队分析 EURUSD 未来两周走势。"
    )
    captured: dict[str, object] = {}

    def fake_execute(decision, *, event_callback):
        captured["decision"] = decision
        captured["event_callback"] = event_callback
        return json.dumps({"status": "completed", "preset": "fx_debate_team"})

    def fail_route(*args, **kwargs):
        del args, kwargs
        raise AssertionError("tool arguments must not be re-routed")

    monkeypatch.setattr(swarm_tool_module, "_execute_fx_debate_request", fake_execute)
    monkeypatch.setattr(swarm_tool_module, "route_fx_prompt", fail_route)

    payload = json.loads(
        SwarmTool(swarm_authorization=authorization).execute(
            prompt="分析 AAPL",
            preset_name="equity_research_team",
        )
    )

    assert payload["preset"] == "fx_debate_team"
    decision = captured["decision"]
    assert decision.request.target == "EURUSD"
    assert decision.request.goal == authorization.raw_user_content


def test_authorized_non_fx_request_stays_on_generic_swarm_path(monkeypatch) -> None:
    import src.tools.swarm_tool as swarm_tool_module

    authorization = build_swarm_authorization(
        "请让团队协作分析苹果公司财报。"
    )

    def fail_route(*args, **kwargs):
        del args, kwargs
        raise AssertionError("tool arguments must not determine FX routing")

    monkeypatch.setattr(swarm_tool_module, "route_fx_prompt", fail_route)
    monkeypatch.setattr(
        swarm_tool_module,
        "_resolve_preset",
        lambda prompt, explicit_preset: (None, f"generic:{prompt}:{explicit_preset}"),
    )

    payload = json.loads(
        SwarmTool(swarm_authorization=authorization).execute(
            prompt="分析 EURUSD",
            preset_name="fx_debate_team",
        )
    )

    assert payload["error"].startswith("generic:")
