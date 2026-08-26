"""SessionService regressions for remote MCP startup paths."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.session.events import EventBus
from src.session.models import Attempt, AttemptStatus, Session
from src.session.service import SessionService, _sanitize_user_reply
from src.session.store import SessionStore


class _DummyIndex:
    def index_session(self, session_id: str, title: str) -> None:
        del session_id, title

    def index_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content


class _DummyAgentLoop:
    def __init__(self, *, registry, llm, event_callback, max_iterations, persistent_memory) -> None:
        del registry, llm, event_callback, max_iterations, persistent_memory

    def run(self, *, user_message: str, history, session_id: str) -> dict[str, str]:
        del user_message, history, session_id
        return {"status": "completed"}


def test_run_with_agent_keeps_event_loop_responsive_during_registry_build(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def _slow_build_registry(**kwargs):
        del kwargs
        time.sleep(0.25)
        return object()

    monkeypatch.setattr("src.session.service.get_shared_index", lambda: _DummyIndex())
    monkeypatch.setattr("src.tools.build_registry", _slow_build_registry)
    monkeypatch.setattr("src.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.memory.persistent.PersistentMemory", lambda: object())
    monkeypatch.setattr("src.agent.loop.AgentLoop", _DummyAgentLoop)
    monkeypatch.setattr("src.config.loader.load_runtime_agent_config", lambda overrides=None: object())
    monkeypatch.setattr("src.config.loader.sanitize_session_overrides", lambda overrides: dict(overrides))

    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    attempt = Attempt(session_id="session-1", prompt="hello")

    async def _ticker(events: list[float], start: float) -> None:
        await asyncio.sleep(0.05)
        events.append(time.perf_counter() - start)

    async def _exercise() -> tuple[list[float], dict[str, str]]:
        events: list[float] = []
        start = time.perf_counter()
        asyncio.create_task(_ticker(events, start))
        result = await service._run_with_agent(attempt, messages=[], session_config={})
        await asyncio.sleep(0.01)
        return events, result

    tick_times, result = asyncio.run(_exercise())

    assert result["status"] == "completed"
    assert tick_times, "Expected the event loop ticker to run while registry build was pending"
    assert tick_times[0] < 0.18, f"Registry build blocked the event loop for too long: {tick_times[0]:.3f}s"


def test_cancel_current_persists_terminal_status_before_agent_loop_is_ready(tmp_path: Path) -> None:
    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    session = Session(session_id="session-1")
    service.store.create_session(session)
    attempt = Attempt(session_id=session.session_id, attempt_id="attempt-1", prompt="hello")
    attempt.mark_running()
    service.store.create_attempt(attempt)
    service._active_attempts[session.session_id] = attempt.attempt_id

    assert service.cancel_current(session.session_id) is True
    assert "attempt-1" in service._cancelled_attempts
    persisted = service.store.get_attempt(session.session_id, attempt.attempt_id)
    assert persisted is not None
    assert persisted.status == AttemptStatus.CANCELLED
    assert persisted.completed_at is not None


def test_swarm_title_updates_default_session_without_overwriting_custom_title(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("src.session.service.get_shared_index", lambda: _DummyIndex())
    monkeypatch.setattr(
        "src.swarm.presets.load_preset",
        lambda name: {"name": name, "title": "Equity Research Team"},
    )
    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    default_session = Session(session_id="default", title="FX Debate")
    custom_session = Session(session_id="custom", title="我的宏观复盘")
    service.store.create_session(default_session)
    service.store.create_session(custom_session)

    service._update_default_session_title(default_session.session_id, "equity_research_team")
    service._update_default_session_title(custom_session.session_id, "equity_research_team")

    assert service.store.get_session("default").title == "Equity Research Team"
    assert service.store.get_session("custom").title == "我的宏观复盘"


def test_send_message_rejects_duplicate_active_attempt(tmp_path: Path) -> None:
    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    session = Session(session_id="session-duplicate")
    service.store.create_session(session)
    attempt = Attempt(session_id=session.session_id, attempt_id="attempt-active", prompt="hello")
    attempt.mark_running()
    service.store.create_attempt(attempt)
    service._active_attempts[session.session_id] = attempt.attempt_id

    with pytest.raises(ValueError, match="SESSION_BUSY"):
        asyncio.run(service.send_message(session.session_id, "再问一次"))


def test_cancel_current_can_close_persisted_attempt_without_memory_handle(tmp_path: Path) -> None:
    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    session = Session(session_id="session-1", last_attempt_id="attempt-1")
    service.store.create_session(session)
    attempt = Attempt(session_id=session.session_id, attempt_id=session.last_attempt_id, prompt="hello")
    attempt.mark_running()
    service.store.create_attempt(attempt)

    assert service.cancel_current(session.session_id) is True
    assert service.store.get_attempt(session.session_id, attempt.attempt_id).status == AttemptStatus.CANCELLED


def test_late_agent_success_cannot_overwrite_cancellation(tmp_path: Path, monkeypatch) -> None:
    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    session = Session(session_id="session-1")
    service.store.create_session(session)
    attempt = Attempt(session_id=session.session_id, attempt_id="attempt-1", prompt="hello")
    service.store.create_attempt(attempt)
    service._active_attempts[session.session_id] = attempt.attempt_id

    async def _late_success(*args, **kwargs):
        del args, kwargs
        assert service.cancel_current(session.session_id) is True
        return {"status": "success", "content": "late result"}

    monkeypatch.setattr(service, "_run_with_agent", _late_success)
    asyncio.run(service._run_attempt(session, attempt))

    persisted = service.store.get_attempt(session.session_id, attempt.attempt_id)
    assert persisted is not None
    assert persisted.status == AttemptStatus.CANCELLED


def test_cancelled_attempt_message_is_not_reported_as_execution_failure(tmp_path: Path) -> None:
    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    attempt = Attempt(session_id="session-1", attempt_id="attempt-1", prompt="hello")
    attempt.mark_cancelled(summary="cancelled by user")

    assert service._format_result_message(attempt) == "运行已取消：本轮研究已停止，未生成最终结论。"


def test_conversation_reply_hides_internal_evidence_diagnostics() -> None:
    attempt = Attempt(
        session_id="session-1",
        attempt_id="attempt-1",
        summary="EURUSD 回测结论为做空。Evidence Context 后端索引异常，无法二次回查。请查看最终报告。",
    )
    attempt.mark_completed(summary=attempt.summary or "")

    reply = SessionService._format_result_message(attempt)

    assert "EURUSD 回测结论为做空" in reply
    assert "请查看最终报告" in reply
    assert "Evidence Context" not in reply
    assert "二次回查" not in reply
    assert "后端索引" not in _sanitize_user_reply(reply)


def test_conversation_reply_hides_confidence_but_keeps_direction() -> None:
    attempt = Attempt(
        session_id="session-1",
        attempt_id="attempt-1",
        summary="最终方向：做空。置信度较低，confidence: 35%。",
    )
    attempt.mark_completed(summary=attempt.summary or "")

    reply = SessionService._format_result_message(attempt)

    assert reply == "最终方向：做空。"
    assert "置信度" not in reply
    assert "confidence" not in reply.lower()


def test_reconcile_incomplete_attempts_marks_restart_orphans_cancelled(tmp_path: Path) -> None:
    service = SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )
    session = Session(session_id="session-1")
    service.store.create_session(session)
    attempt = Attempt(session_id=session.session_id, attempt_id="attempt-1", prompt="hello")
    attempt.mark_running()
    service.store.create_attempt(attempt)

    assert service.reconcile_incomplete_attempts() == 1
    persisted = service.store.get_attempt(session.session_id, attempt.attempt_id)
    assert persisted is not None
    assert persisted.status == AttemptStatus.CANCELLED
    assert persisted.summary == "interrupted by API restart"
