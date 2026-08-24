"""CLI AgentLoop wiring for current-prompt Swarm authorization."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("请让团队分析 EURUSD 未来两周走势。", True),
        ("分析 EURUSD 未来两周走势。", False),
    ],
)
def test_cli_agent_registry_uses_only_current_prompt(
    monkeypatch,
    prompt: str,
    expected: bool,
) -> None:
    from cli import _legacy

    captured: dict[str, object] = {}

    def build_registry(**kwargs):
        captured.update(kwargs)
        return object()

    class StubLoop:
        def __init__(self, **kwargs):
            del kwargs
            self.memory = SimpleNamespace(run_dir=None)

        def run(self, **kwargs):
            del kwargs
            return {"status": "completed"}

    monkeypatch.setattr("src.tools.build_registry", build_registry)
    monkeypatch.setattr("src.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.agent.loop.AgentLoop", StubLoop)
    monkeypatch.setattr(
        "src.memory.persistent.PersistentMemory",
        lambda: SimpleNamespace(run_dir=None),
    )
    monkeypatch.setattr("src.config.loader.load_agent_config", lambda: object())

    _legacy._run_agent(prompt, stream_output=False)

    authorization = captured["swarm_authorization"]
    assert authorization.authorized is expected
    assert authorization.raw_user_content == prompt
