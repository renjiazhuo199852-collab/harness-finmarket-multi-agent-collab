"""Session attempt isolation for raw-message Swarm authorization."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from src.session.events import EventBus
from src.session.models import Attempt
from src.session.service import SessionService
from src.session.store import SessionStore


class _DummyIndex:
    def index_session(self, session_id: str, title: str) -> None:
        del session_id, title

    def index_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content


class _AuthorizationEchoAgent:
    def __init__(self, *, registry, llm, event_callback, max_iterations, persistent_memory) -> None:
        del llm, event_callback, max_iterations, persistent_memory
        self.authorization = registry

    def run(self, *, user_message: str, history, session_id: str) -> dict[str, object]:
        del history
        return {
            "status": "completed",
            "session_id": session_id,
            "user_message": user_message,
            "authorized": self.authorization.authorized,
            "authorization_prompt": self.authorization.raw_user_content,
        }


def test_concurrent_attempts_do_not_share_swarm_authorization(
    monkeypatch,
    tmp_path: Path,
) -> None:
    barrier = threading.Barrier(2)
    captured: dict[str, object] = {}

    def build_registry(**kwargs):
        authorization = kwargs["swarm_authorization"]
        captured[authorization.raw_user_content] = authorization
        barrier.wait(timeout=5)
        return authorization

    monkeypatch.setattr("src.session.service.get_shared_index", lambda: _DummyIndex())
    monkeypatch.setattr("src.tools.build_registry", build_registry)
    monkeypatch.setattr("src.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.memory.persistent.PersistentMemory", lambda: object())
    monkeypatch.setattr("src.agent.loop.AgentLoop", _AuthorizationEchoAgent)
    monkeypatch.setattr("src.config.loader.load_runtime_agent_config", lambda overrides=None: object())
    monkeypatch.setattr("src.config.loader.sanitize_session_overrides", lambda overrides: dict(overrides))

    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    authorized_prompt = "请让团队分析 EURUSD 未来两周走势。"
    unauthorized_prompt = "分析 EURUSD 未来两周走势。"
    authorized_attempt = Attempt(session_id="session-authorized", prompt=authorized_prompt)
    unauthorized_attempt = Attempt(session_id="session-unauthorized", prompt=unauthorized_prompt)

    async def exercise():
        return await asyncio.gather(
            service._run_with_agent(
                authorized_attempt,
                messages=[],
                session_config={},
            ),
            service._run_with_agent(
                unauthorized_attempt,
                messages=[
                    {"role": "user", "content": "上一轮请让团队分析 EURUSD。"},
                ],
                session_config={},
            ),
        )

    authorized_result, unauthorized_result = asyncio.run(exercise())

    assert authorized_result["authorized"] is True
    assert authorized_result["authorization_prompt"] == authorized_prompt
    assert unauthorized_result["authorized"] is False
    assert unauthorized_result["authorization_prompt"] == unauthorized_prompt
    assert captured[authorized_prompt] is not captured[unauthorized_prompt]
