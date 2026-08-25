"""Session lifecycle orchestration for message flow, attempt creation, and execution scheduling.

V5: Uses AgentLoop instead of the fixed pipeline behind the generate skill.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Dedicated thread pool limited to four concurrent agents to avoid exhausting the default executor.
_AGENT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")

_INTERNAL_REPLY_DIAGNOSTIC = re.compile(
    r"Evidence\s+Context|evidence_context_id|证据上下文|后端索引|索引异常|"
    r"二次回查|无法回查|校验失败|validate_fx_output|tool_calls|trace_id|request_id",
    re.IGNORECASE,
)
_INTERNAL_REPLY_SENTENCE = re.compile(
    r"(?:^|(?<=[。！？.!?\n]))\s*[^。！？.!?\n]*?(?:Evidence\s+Context|evidence_context_id|证据上下文|后端索引|"
    r"索引异常|二次回查|无法回查|校验失败|validate_fx_output|tool_calls|trace_id|request_id)"
    r"[^。！？.!?\n]*[。！？.!?]?",
    re.IGNORECASE,
)


def _sanitize_user_reply(content: str | None) -> str:
    """Hide internal evidence plumbing from the conversational reply.

    Detailed diagnostics remain in the run event stream and persisted report.
    This boundary only controls the assistant bubble shown in a conversation.
    """
    text = str(content or "").strip()
    if not text:
        return text
    text = _INTERNAL_REPLY_SENTENCE.sub("", text)
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not _INTERNAL_REPLY_DIAGNOSTIC.search(line)
    ]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or "本轮研究已完成，最终结果已生成。"

from src.session.events import EventBus
from src.session.models import (
    Attempt,
    AttemptStatus,
    Message,
    Session,
)
from src.session.search import get_shared_index
from src.session.store import SessionStore


class SessionService:
    """Session lifecycle service.

    Attributes:
        store: Session persistence store.
        event_bus: SSE event bus.
        runs_dir: Root runs directory.
    """

    def __init__(
        self,
        store: SessionStore,
        event_bus: EventBus,
        runs_dir: Path,
    ) -> None:
        """Initialize the session service.

        Args:
            store: Session persistence store.
            event_bus: SSE event bus.
            runs_dir: Root runs directory.
        """
        self.store = store
        self.event_bus = event_bus
        self.runs_dir = runs_dir
        self._active_loops: Dict[str, "AgentLoop"] = {}
        self._active_attempts: Dict[str, str] = {}
        self._cancelled_attempts: set[str] = set()
        self._search_index = get_shared_index()

    def create_session(self, title: str = "", config: Optional[Dict[str, Any]] = None) -> Session:
        """Create a new session.

        Args:
            title: Session title.
            config: Session configuration.

        Returns:
            The newly created Session.
        """
        session = Session(title=title, config=config or {})
        self.store.create_session(session)
        self._search_index.index_session(session.session_id, title)
        self.event_bus.emit(session.session_id, "session.created", {"session_id": session.session_id, "title": title})
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Return a session by ID."""
        return self.store.get_session(session_id)

    def list_sessions(self, limit: int = 50) -> list[Session]:
        """List all sessions."""
        return self.store.list_sessions(limit)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        self.event_bus.clear(session_id)
        return self.store.delete_session(session_id)

    @staticmethod
    def _swarm_preset_title(preset_name: str) -> str:
        """Resolve the human-readable title for a swarm preset."""
        try:
            from src.swarm.presets import load_preset

            preset = load_preset(preset_name)
            title = preset.get("title") if isinstance(preset, dict) else None
            return str(title or preset_name)
        except Exception:
            # Preset metadata is presentation-only; it must not block a run.
            return preset_name

    def _update_default_session_title(self, session_id: str, preset_name: str) -> None:
        """Name a default chat session after the swarm it actually launched."""
        session = self.store.get_session(session_id)
        if session is None or session.title.strip() not in {"", "FX Debate"}:
            return
        title = self._swarm_preset_title(preset_name).strip()
        if not title:
            return
        session.title = title
        session.updated_at = datetime.now().isoformat()
        self.store.update_session(session)
        self._search_index.index_session(session.session_id, title)

    def reconcile_incomplete_attempts(self) -> int:
        """Close attempts left running by a previous API process.

        Session attempts execute in process memory.  If the API is restarted,
        an attempt persisted as ``running`` can no longer make progress, so it
        must not remain indefinitely active in the history sidebar.

        Returns:
            Number of attempts marked cancelled during reconciliation.
        """
        reconciled = 0
        for session in self.store.list_sessions(limit=100_000):
            changed = False
            for attempt in self.store.list_attempts(session.session_id, limit=100_000):
                if attempt.status not in {
                    AttemptStatus.PENDING,
                    AttemptStatus.RUNNING,
                    AttemptStatus.WAITING_USER,
                }:
                    continue
                attempt.mark_cancelled(summary="interrupted by API restart")
                self.store.update_attempt(attempt)
                changed = True
                reconciled += 1
            if changed:
                session.updated_at = datetime.now().isoformat()
                self.store.update_session(session)
        return reconciled

    async def send_message(
        self,
        session_id: str,
        content: str,
        role: str = "user",
        *,
        include_shell_tools: bool = False,
    ) -> Dict[str, Any]:
        """Send a message to a session and trigger execution.

        Args:
            session_id: Session ID.
            content: Message content.
            role: Message role.
            include_shell_tools: Whether this attempt may use shell tools.

        Returns:
            Dictionary containing message_id and attempt_id.
        """
        session = self.store.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # A session owns one active user turn.  Without this guard a double
        # click, reconnect, or stale frontend submit can create two attempts
        # that both route the same question and compete for the same SSE
        # stream.  Terminal attempts remain resumable as a new turn.
        active_id = self._active_attempts.get(session_id)
        if active_id:
            active = self.store.get_attempt(session_id, active_id)
            if active is not None and active.status in {
                AttemptStatus.PENDING,
                AttemptStatus.RUNNING,
                AttemptStatus.WAITING_USER,
            }:
                raise ValueError("SESSION_BUSY: 当前对话仍有运行中的任务，请先停止或等待完成。")

        message = Message(session_id=session_id, role=role, content=content)
        self.store.append_message(message)
        self._search_index.index_message(session_id, role, content)
        self.event_bus.emit(session_id, "message.received", {"message_id": message.message_id, "role": role, "content": content})

        if role != "user":
            return {"message_id": message.message_id}

        attempt = Attempt(session_id=session_id, parent_attempt_id=session.last_attempt_id, prompt=content)
        self.store.create_attempt(attempt)
        session.config["include_shell_tools"] = include_shell_tools
        session.last_attempt_id = attempt.attempt_id
        session.updated_at = datetime.now().isoformat()
        self.store.update_session(session)
        self._active_attempts[session_id] = attempt.attempt_id
        self.event_bus.emit(session_id, "attempt.created", {"attempt_id": attempt.attempt_id, "prompt": content})

        asyncio.create_task(self._run_attempt(session, attempt, include_shell_tools=include_shell_tools))
        return {"message_id": message.message_id, "attempt_id": attempt.attempt_id}

    def get_messages(self, session_id: str, limit: int = 100) -> list[Message]:
        """Return the message history."""
        return self.store.get_messages(session_id, limit)

    def cancel_current(self, session_id: str) -> bool:
        """Cancel the current attempt for a session.

        Args:
            session_id: Session ID.

        Returns:
            Whether cancellation was recorded. This also covers the short
            window before the AgentLoop has finished building its registry.
        """
        attempt_id = self._active_attempts.get(session_id)
        loop = self._active_loops.get(session_id)
        if attempt_id is None:
            # A persisted running attempt may outlive the in-memory maps after
            # an API restart.  It is still safe to close it explicitly.
            session = self.store.get_session(session_id)
            attempt_id = getattr(session, "last_attempt_id", None) if session else None
        if attempt_id is None:
            return False

        attempt = self.store.get_attempt(session_id, attempt_id)
        if attempt is None or attempt.status in {
            AttemptStatus.COMPLETED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
        }:
            return False

        self._cancelled_attempts.add(attempt_id)
        # Persist the terminal state before returning from the HTTP endpoint.
        # Registry/MCP setup runs in a worker thread and may take longer to
        # observe the cancellation flag; the UI and history must converge now.
        attempt.mark_cancelled(summary="cancelled by user")
        self.store.update_attempt(attempt)
        session = self.store.get_session(session_id)
        if session is not None:
            session.updated_at = datetime.now().isoformat()
            self.store.update_session(session)
        if loop is not None:
            loop.cancel()
        return True

    async def _run_attempt(self, session: Session, attempt: Attempt, *, include_shell_tools: bool = False) -> None:
        """Execute an Attempt in the background."""
        try:
            # The cancel endpoint can run before this task gets its first
            # scheduling turn.  Do not transition a persisted cancellation back
            # to running in that race window.
            if self._attempt_is_cancelled(attempt):
                result = {"status": "cancelled", "reason": "cancelled by user"}
            else:
                attempt.mark_running()
                self.store.update_attempt(attempt)
                self.event_bus.emit(session.session_id, "attempt.started", {"attempt_id": attempt.attempt_id})
                messages = self.store.get_messages(session.session_id)
                if attempt.attempt_id in self._cancelled_attempts:
                    result = {"status": "cancelled", "reason": "cancelled by user"}
                else:
                    result = await self._run_with_agent(
                        attempt,
                        messages=messages,
                        include_shell_tools=include_shell_tools,
                        session_config=dict(session.config),
                    )

            # Cancellation is terminal from the user's perspective even if a
            # worker returns a late success after its provider call unwinds.
            if self._attempt_is_cancelled(attempt):
                result = {"status": "cancelled", "reason": "cancelled by user"}
            if result.get("status") == "success":
                attempt.mark_completed(summary=result.get("content", ""))
            elif result.get("status") == "cancelled":
                attempt.mark_cancelled(summary=result.get("reason", "cancelled by user"))
            else:
                attempt.mark_failed(error=result.get("reason", "unknown"))
            attempt.run_dir = result.get("run_dir")
            attempt.swarm_run_id = result.get("swarm_run_id") or attempt.swarm_run_id

            self.store.update_attempt(attempt)
            reply_metadata = {}
            if attempt.run_dir:
                reply_metadata["run_id"] = Path(attempt.run_dir).name
            if attempt.swarm_run_id:
                reply_metadata["swarm_run_id"] = attempt.swarm_run_id
            reply_metadata["status"] = attempt.status.value
            if attempt.metrics:
                reply_metadata["metrics"] = attempt.metrics

            reply = Message(
                session_id=session.session_id, role="assistant",
                content=self._format_result_message(attempt),
                linked_attempt_id=attempt.attempt_id,
                metadata=reply_metadata,
            )
            self.store.append_message(reply)
            self._search_index.index_message(session.session_id, "assistant", reply.content)
            self.event_bus.emit(
                session.session_id,
                "attempt.completed" if attempt.status == AttemptStatus.COMPLETED else "attempt.failed",
                {"attempt_id": attempt.attempt_id, "status": attempt.status.value,
                 "summary": attempt.summary, "error": attempt.error, "run_dir": attempt.run_dir},
            )

        except Exception as exc:
            if self._attempt_is_cancelled(attempt):
                attempt.mark_cancelled(summary="cancelled by user")
            else:
                attempt.mark_failed(error=str(exc))
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                session.session_id,
                "attempt.failed",
                {
                    "attempt_id": attempt.attempt_id,
                    "status": attempt.status.value,
                    "error": None if attempt.status == AttemptStatus.CANCELLED else str(exc),
                },
            )
        finally:
            # The early-cancel path can finish before _run_with_agent installs
            # its inner cleanup block. Keep the per-attempt cancellation state
            # bounded and do not disturb a newer attempt in this session.
            if self._active_attempts.get(session.session_id) == attempt.attempt_id:
                self._active_attempts.pop(session.session_id, None)
            self._cancelled_attempts.discard(attempt.attempt_id)

    def _attempt_is_cancelled(self, attempt: Attempt) -> bool:
        """Return whether cancellation was requested for this attempt."""
        if attempt.attempt_id in self._cancelled_attempts:
            return True
        persisted = self.store.get_attempt(attempt.session_id, attempt.attempt_id)
        return bool(persisted and persisted.status == AttemptStatus.CANCELLED)

    async def _run_with_agent(
        self,
        attempt: Attempt,
        messages: list = None,
        *,
        include_shell_tools: bool = False,
        session_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an attempt with the V5 AgentLoop.

        Args:
            attempt: Current execution attempt.
            messages: Session message history.
            include_shell_tools: Whether the registry may include shell tools.
            session_config: Optional session-level config overrides. MCP server
                definitions under the ``mcpServers`` key are merged on top of
                the user config file via ``load_runtime_agent_config`` so each
                session can extend or override the global MCP server list.

        Returns:
            Result dictionary containing status, run_dir, run_id, metrics, and related fields.
        """
        from src.tools import build_registry
        from src.agent.swarm_authorization import build_swarm_authorization
        from src.providers.chat import ChatLLM
        from src.agent.loop import AgentLoop
        from src.memory.persistent import PersistentMemory
        from src.config.loader import load_runtime_agent_config, sanitize_session_overrides

        llm = ChatLLM()
        pm = PersistentMemory()

        session_id = attempt.session_id
        attempt_id = attempt.attempt_id
        swarm_authorization = build_swarm_authorization(attempt.prompt)
        loop = asyncio.get_running_loop()

        safe_overrides = sanitize_session_overrides(session_config) if session_config else session_config
        agent_config = load_runtime_agent_config(overrides=safe_overrides)

        def event_callback(event_type: str, data: Dict[str, Any]) -> None:
            """Forward AgentLoop events to the SSE event bus."""
            data["attempt_id"] = attempt_id
            if event_type == "swarm.started":
                run_id = data.get("run_id")
                if isinstance(run_id, str) and run_id:
                    attempt.swarm_run_id = run_id
                    self.store.update_attempt(attempt)
                preset_name = data.get("preset")
                if isinstance(preset_name, str) and preset_name:
                    self._update_default_session_title(session_id, preset_name)
            # ``tool_result`` deliberately carries a bounded preview on SSE.
            # FX Debate places its canonical swarm id near the beginning of
            # that JSON preview, so capture it at the attempt boundary while
            # preserving the existing event contract.
            if event_type == "tool_result" and data.get("tool") in {"run_fx_debate", "swarm"}:
                preview = data.get("preview")
                if isinstance(preview, str):
                    match = re.search(r'"run_id"\s*:\s*"(swarm-[^"]+)"', preview)
                    if match:
                        attempt.swarm_run_id = match.group(1)
                        self.store.update_attempt(attempt)
            self.event_bus.emit(session_id, event_type, data)

        def _mcp_collision_warn(msg: str) -> None:
            """Forward MCP server-name collision warnings to the operator event channel."""
            self.event_bus.emit(session_id, "mcp.warning", {"attempt_id": attempt_id, "message": msg})

        agent_ref: list[AgentLoop | None] = [None]

        def cancel_checker() -> bool:
            current = agent_ref[0]
            return attempt_id in self._cancelled_attempts or (
                current.is_cancelled() if current is not None else False
            )

        registry = await loop.run_in_executor(
            _AGENT_EXECUTOR,
            lambda: build_registry(
                persistent_memory=pm,
                include_shell_tools=include_shell_tools,
                agent_config=agent_config,
                session_id=session_id,
                event_callback=event_callback,
                cancel_checker=cancel_checker,
                swarm_authorization=swarm_authorization,
                warn_callback=_mcp_collision_warn,
            ),
        )

        agent = AgentLoop(
            registry=registry,
            llm=llm,
            event_callback=event_callback,
            max_iterations=50,
            persistent_memory=pm,
        )
        agent_ref[0] = agent
        self._active_loops[session_id] = agent
        if attempt_id in self._cancelled_attempts:
            agent.cancel()

        # Build the message history context.
        history = self._convert_messages_to_history(messages) if messages else None

        try:
            result = await loop.run_in_executor(
                _AGENT_EXECUTOR,
                lambda: agent.run(
                    user_message=attempt.prompt,
                    history=history,
                    session_id=session_id,
                ),
            )
        finally:
            # A cancelled attempt may overlap a newly submitted attempt after
            # the UI is unlocked. Do not remove the newer loop's cancellation
            # handle when the older executor finishes.
            if self._active_loops.get(session_id) is agent:
                self._active_loops.pop(session_id, None)
            if self._active_attempts.get(session_id) == attempt_id:
                self._active_attempts.pop(session_id, None)
        # Load metrics from the run output when available.
        if result.get("run_dir"):
            metrics = self._load_metrics(Path(result["run_dir"]))
            if metrics:
                result["metrics"] = metrics

        return result

    @staticmethod
    def _convert_messages_to_history(messages: list) -> list[Dict[str, Any]]:
        """Convert Session messages into OpenAI-format history.

        Keeps the readable ``[prev_run: {run_id}]`` marker instead of removing it
        completely, and trims by character budget instead of a hard six-message cap
        so the LLM can still see previous artifact paths and strategy content during
        iterative updates.

        Args:
            messages: Session message list without the current turn.

        Returns:
            OpenAI-format messages trimmed from the newest items within the token budget.
        """
        import re
        from pathlib import Path

        def _shorten_run_dir(match: re.Match) -> str:
            path_str = match.group(0).replace("Run directory:", "").strip()
            run_id = Path(path_str).name if path_str else ""
            return f"[prev_run: {run_id}]" if run_id else ""

        history = []
        for msg in messages[:-1]:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            if not content.strip() or role not in ("user", "assistant"):
                continue
            content = re.sub(r"Run directory:\s*\S+", _shorten_run_dir, content).strip()
            if content:
                history.append({"role": role, "content": content})

        # Trim from the newest messages within a character budget of roughly 3000 tokens.
        MAX_HISTORY_CHARS = 12000
        total_chars = 0
        trimmed: list = []
        for msg in reversed(history):
            msg_len = len(msg.get("content", ""))
            if total_chars + msg_len > MAX_HISTORY_CHARS:
                break
            trimmed.append(msg)
            total_chars += msg_len
        return list(reversed(trimmed))

    @staticmethod
    def _load_metrics(run_dir: Path) -> Optional[Dict[str, Any]]:
        """Load metrics.csv from a run directory."""
        import csv
        metrics_path = run_dir / "artifacts" / "metrics.csv"
        if not metrics_path.exists():
            return None
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    return {k: float(v) for k, v in rows[0].items() if v}
        except Exception:
            pass
        return None

    @staticmethod
    def _format_result_message(attempt: Attempt) -> str:
        """Format the final execution result message."""
        if attempt.status == AttemptStatus.COMPLETED:
            return _sanitize_user_reply(attempt.summary or "Strategy execution completed.")
        if attempt.status == AttemptStatus.CANCELLED:
            return "运行已取消：本轮研究已停止，未生成最终结论。"
        error = attempt.error or "unknown error"
        if _INTERNAL_REPLY_DIAGNOSTIC.search(error):
            return "本轮研究未生成最终结果，请重新运行。"
        return f"Execution failed: {error}"
