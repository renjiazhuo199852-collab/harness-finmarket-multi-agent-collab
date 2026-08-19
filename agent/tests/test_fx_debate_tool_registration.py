"""配置内部数据库后 FX Debate 六个领域 Tool 的注册测试。"""

from __future__ import annotations

from src import tools as tools_package


def test_configured_registry_exposes_only_public_fx_debate_seams(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_DB_ENABLED", "true")
    monkeypatch.setenv("MARKET_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("MARKET_DB_PORT", "15433")
    monkeypatch.setenv("MARKET_DB_NAME", "icbc_shared")
    monkeypatch.setenv("MARKET_DB_USER", "icbc_collab")
    monkeypatch.setenv("MARKET_DB_PASSWORD", "test-password")
    monkeypatch.setenv("FX_DEBATE_DATA_SOURCE", "database")

    previous_cache = tools_package._SUBCLASSES_CACHE
    try:
        tools_package._SUBCLASSES_CACHE = None
        registry = tools_package.build_registry()
    finally:
        tools_package._SUBCLASSES_CACHE = previous_cache

    assert {
        "run_fx_debate",
        "get_fx_market_evidence",
        "get_fx_macro_evidence",
        "get_fx_news_evidence",
        "get_fx_evidence_manifest",
        "get_fx_relative_macro_scorecard",
        "get_fx_technical_regime",
        "get_fx_story_clusters",
        "get_fx_evidence_by_ids",
        "validate_fx_output",
    }.issubset(registry.tool_names)


def test_registry_injects_session_event_callback_into_fx_debate(monkeypatch) -> None:
    from src.tools.run_fx_debate_tool import RunFxDebateTool

    monkeypatch.setattr(
        RunFxDebateTool,
        "check_available",
        classmethod(lambda cls: True),
    )
    previous_cache = tools_package._SUBCLASSES_CACHE
    try:
        tools_package._SUBCLASSES_CACHE = None
        events: list[tuple[str, dict]] = []

        def callback(event_type: str, data: dict) -> None:
            events.append((event_type, data))

        registry = tools_package.build_registry(event_callback=callback)
    finally:
        tools_package._SUBCLASSES_CACHE = previous_cache

    tool = registry.get("run_fx_debate")
    assert tool is not None
    adapted = getattr(tool, "_event_callback")
    assert adapted is not None
    adapted({"type": "context_ready", "data": {"source": "test"}})
    adapted(
        {
            "type": "swarm_started",
            "run_id": "run-1",
            "preset": "fx_debate_team",
            "agents": [],
            "tasks": [],
        }
    )
    adapted({"type": "task_started", "run_id": "run-1"})
    assert events == [
        ("fx_debate.context_ready", {"type": "context_ready", "data": {"source": "test"}}),
        (
            "swarm.started",
            {
                "run_id": "run-1",
                "preset": "fx_debate_team",
                "agents": [],
                "tasks": [],
            },
        ),
        (
            "swarm.event",
            {"run_id": "run-1", "event": {"type": "task_started", "run_id": "run-1"}},
        ),
    ]


def test_registry_injects_cooperative_cancellation_checker(monkeypatch) -> None:
    from src.tools.run_fx_debate_tool import RunFxDebateTool

    monkeypatch.setattr(
        RunFxDebateTool,
        "check_available",
        classmethod(lambda cls: True),
    )
    previous_cache = tools_package._SUBCLASSES_CACHE
    try:
        tools_package._SUBCLASSES_CACHE = None
        def checker() -> bool:
            return True

        registry = tools_package.build_registry(cancel_checker=checker)
    finally:
        tools_package._SUBCLASSES_CACHE = previous_cache

    tool = registry.get("run_fx_debate")
    assert tool is not None
    assert getattr(tool, "_cancel_checker") is checker
