"""Local-only HTTP server for manually exercising the five-Agent FX Debate."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from src.tools.redaction import redact_payload

_AGENT_DIR = Path(__file__).resolve().parent
_UI_DIR = _AGENT_DIR / "fx_debate_test_ui"


@dataclass(frozen=True)
class Readiness:
    """Secret-free readiness state displayed by the test frontend."""

    data_ready: bool
    data_source: Literal["database", "excel"]
    llm_ready: bool
    provider: str
    model: str


class RunRequest(BaseModel):
    """Small, safe input surface exposed by the local test page."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="EUR/USD", min_length=6, max_length=20)
    horizon_count: int = Field(ge=1, le=90)
    horizon_unit: Literal["days", "weeks"] = "weeks"
    timeframe: Literal["4H", "1D", "4H/1D"] = "4H/1D"
    risk_profile: Literal["conservative", "balanced", "aggressive"] = "balanced"
    request_id: str | None = Field(default=None, max_length=80)
    conversation_id: str | None = Field(default=None, max_length=80)
    goal: str | None = Field(default=None, max_length=4000)
    as_of: datetime | None = None
    confirm_cost: Literal[True]

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_horizon(self) -> "RunRequest":
        days = self.horizon_count * (7 if self.horizon_unit == "weeks" else 1)
        if days > 90:
            raise ValueError("horizon must resolve to at most 90 days")
        return self

    @field_validator("target")
    @classmethod
    def normalize_target(cls, value: str) -> str:
        normalized = "".join(value.strip().upper().split())
        if len(normalized.replace("/", "").replace("-", "")) != 6:
            raise ValueError("target must be a six-letter FX pair, for example EUR/USD")
        return normalized


class LocalSettingsRequest(BaseModel):
    """Loopback-only settings payload for the standalone test console."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    reasoning_effort: (
        Literal["", "none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    ) = None
    data_source: Literal["excel", "database"] | None = None
    excel_path: str | None = Field(default=None, max_length=1000)
    db_host: str | None = Field(default=None, max_length=200)
    db_port: int | None = Field(default=None, ge=1, le=65535)
    db_name: str | None = Field(default=None, max_length=200)
    db_user: str | None = Field(default=None, max_length=200)
    db_password: str | None = Field(default=None, max_length=1000)


class DebateLauncher(Protocol):
    """Boundary between the local UI and the existing Debate Tool."""

    def __call__(
        self,
        public_request: dict[str, Any],
        run_options: dict[str, Any],
        emit: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        """Run the existing Tool and return its decoded JSON result."""


@dataclass
class JobRecord:
    """In-memory status for one local manual test."""

    job_id: str
    conversation_id: str
    status: Literal["queued", "running", "completed", "failed"]
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class ActiveRunError(RuntimeError):
    """Raised when a second paid run is requested while one is active."""


class HistoryStore:
    """File-backed history adapter for the standalone local console."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (_AGENT_DIR / ".fx_debate_ui")
        self.jobs_dir = self.root / "jobs"
        self.index_path = self.root / "conversations.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def ensure_conversation(self, conversation_id: str | None, title: str) -> str:
        cid = conversation_id or f"fxconv-{uuid.uuid4().hex[:12]}"
        with self._lock:
            index = self._read_index()
            item = index.get(cid) or {
                "conversation_id": cid,
                "title": title[:80] or "FX Debate",
                "created_at": _now(),
                "updated_at": _now(),
                "jobs": [],
            }
            item["updated_at"] = _now()
            index[cid] = item
            self._write_index(index)
        return cid

    def save_job(self, job: JobRecord) -> None:
        with self._lock:
            (self.jobs_dir / f"{job.job_id}.json").write_text(
                json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            index = self._read_index()
            item = index.setdefault(
                job.conversation_id,
                {
                    "conversation_id": job.conversation_id,
                    "title": "FX Debate",
                    "created_at": job.created_at,
                    "updated_at": job.created_at,
                    "jobs": [],
                },
            )
            if job.job_id not in item["jobs"]:
                item["jobs"].append(job.job_id)
            item["updated_at"] = job.completed_at or job.started_at or job.created_at
            item["last_job_id"] = job.job_id
            item["last_status"] = job.status
            self._write_index(index)

    def save_event(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            with (self.jobs_dir / f"{job_id}.events.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def get_job(self, job_id: str) -> JobRecord | None:
        try:
            return JobRecord(
                **json.loads(
                    (self.jobs_dir / f"{job_id}.json").read_text(encoding="utf-8")
                )
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def get_events(self, job_id: str) -> list[dict[str, Any]] | None:
        path = self.jobs_dir / f"{job_id}.events.jsonl"
        if not path.exists():
            return [] if self.get_job(job_id) is not None else None
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return events
        return events

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                self._read_index().values(),
                key=lambda item: item.get("updated_at", ""),
                reverse=True,
            )

    def recover_orphaned_jobs(self) -> int:
        """Mark queued/running jobs as interrupted after a server restart."""
        recovered = 0
        with self._lock:
            index = self._read_index()
            for item in index.values():
                for job_id in item.get("jobs", []):
                    job_path = self.jobs_dir / f"{job_id}.json"
                    try:
                        payload = json.loads(job_path.read_text(encoding="utf-8"))
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if payload.get("status") not in {"queued", "running"}:
                        continue
                    message = "本地服务已重启，上一轮任务被中断；请重新启动 Debate。"
                    payload.update(
                        status="failed",
                        completed_at=_now(),
                        error=message,
                    )
                    job_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    event_path = self.jobs_dir / f"{job_id}.events.jsonl"
                    try:
                        existing_lines = event_path.read_text(
                            encoding="utf-8"
                        ).splitlines()
                    except OSError:
                        existing_lines = []
                    with event_path.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "sequence": len(existing_lines) + 1,
                                    "timestamp": _now(),
                                    "type": "run_recovered",
                                    "agent_id": None,
                                    "task_id": None,
                                    "data": {"status": "failed", "error": message},
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                    item["last_status"] = "failed"
                    item["updated_at"] = payload["completed_at"]
                    recovered += 1
            if recovered:
                self._write_index(index)
        return recovered

    def rename(self, conversation_id: str, title: str) -> bool:
        with self._lock:
            index = self._read_index()
            if conversation_id not in index:
                return False
            index[conversation_id]["title"] = title[:80]
            index[conversation_id]["updated_at"] = _now()
            self._write_index(index)
        return True

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            index = self._read_index()
            item = index.pop(conversation_id, None)
            if item is None:
                return False
            for job_id in item.get("jobs", []):
                for suffix in (".json", ".events.jsonl"):
                    try:
                        (self.jobs_dir / f"{job_id}{suffix}").unlink(missing_ok=True)
                    except OSError:
                        pass
            self._write_index(index)
        return True

    def _read_index(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _write_index(self, index: dict[str, dict[str, Any]]) -> None:
        self.index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class JobManager:
    """Single-flight, process-local runner for manual Debate tests."""

    def __init__(
        self, launcher: DebateLauncher, history: HistoryStore | None = None
    ) -> None:
        self._launcher = launcher
        self._history = history or HistoryStore()
        self._jobs: dict[str, JobRecord] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def start(self, request: RunRequest) -> JobRecord:
        with self._lock:
            if any(job.status in {"queued", "running"} for job in self._jobs.values()):
                raise ActiveRunError("已有一个 FX Debate 正在运行，请等待其完成。")
            job = JobRecord(
                job_id=f"fxui-{uuid.uuid4().hex[:12]}",
                conversation_id=self._history.ensure_conversation(
                    request.conversation_id,
                    _conversation_title(request),
                ),
                status="queued",
                created_at=_now(),
            )
            self._jobs[job.job_id] = job
            self._events[job.job_id] = []
            self._history.save_job(job)
        thread = threading.Thread(
            target=self._execute,
            args=(job.job_id, request),
            name=f"fx-debate-ui-{job.job_id}",
            daemon=True,
        )
        thread.start()
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return JobRecord(**asdict(job))
        return self._history.get_job(job_id)

    def events(self, job_id: str, after: int) -> list[dict[str, Any]] | None:
        with self._lock:
            if job_id in self._events:
                return [dict(event) for event in self._events[job_id][after:]]
        events = self._history.get_events(job_id)
        return None if events is None else events[after:]

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a completed/failed conversation from disk and this process."""
        with self._lock:
            conversation_jobs = [
                job_id
                for job_id, job in self._jobs.items()
                if job.conversation_id == conversation_id
            ]
            if any(
                self._jobs[job_id].status in {"queued", "running"}
                for job_id in conversation_jobs
            ):
                raise ActiveRunError("运行中的会话不能删除，请等待任务结束。")
            if not self._history.delete(conversation_id):
                return False
            for job_id in conversation_jobs:
                self._jobs.pop(job_id, None)
                self._events.pop(job_id, None)
            return True

    def _execute(self, job_id: str, request: RunRequest) -> None:
        self._update(job_id, status="running", started_at=_now())
        try:
            result = self._launcher(
                _public_request(request),
                _run_options(request),
                lambda event: self._append_event(job_id, event),
            )
            if result.get("ok") is True and result.get("status") == "completed":
                self._update(
                    job_id,
                    status="completed",
                    completed_at=_now(),
                    result=result,
                )
            else:
                message = _result_error(result)
                self._update(
                    job_id,
                    status="failed",
                    completed_at=_now(),
                    result=result,
                    error=message,
                )
        except Exception as exc:  # noqa: BLE001 - local runner must retain failure
            self._update(
                job_id,
                status="failed",
                completed_at=_now(),
                error=str(exc),
            )

    def _append_event(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            events = self._events[job_id]
            normalized = {
                "sequence": len(events) + 1,
                "timestamp": str(event.get("timestamp") or _now()),
                "type": str(event.get("type") or "unknown"),
                "agent_id": event.get("agent_id"),
                "task_id": event.get("task_id"),
                "data": _safe_event_data(event.get("data")),
            }
            events.append(normalized)
            self._history.save_event(job_id, normalized)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            self._history.save_job(job)


def probe_readiness() -> Readiness:
    """Inspect local configuration without connecting or exposing credentials."""
    _load_local_env()
    from src.config.accessor import get_env_config
    from src.providers.capabilities import get_llm_credentials
    from src.tools.run_fx_debate_tool import RunFxDebateTool

    config = get_env_config()
    provider = config.llm.langchain_provider.strip()
    model = config.llm.langchain_model_name.strip()
    credentials = get_llm_credentials(provider, model)
    return Readiness(
        data_ready=RunFxDebateTool.check_available(),
        data_source=config.fx_debate.data_source,
        llm_ready=bool(model and credentials["api_key"]),
        provider=provider,
        model=model,
    )


def launch_debate(
    public_request: dict[str, Any],
    run_options: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Call the existing public Tool; this is the only paid/data execution seam."""
    _load_local_env()
    from src.tools.run_fx_debate_tool import RunFxDebateTool

    return json.loads(
        RunFxDebateTool(event_callback=emit).execute(
            **public_request,
            run_options=run_options,
        )
    )


def create_app(
    *,
    readiness_probe: Callable[[], Readiness] = probe_readiness,
    launcher: DebateLauncher = launch_debate,
    history: HistoryStore | None = None,
) -> FastAPI:
    """Create the independent local test application."""
    history_store = history or HistoryStore()
    history_store.recover_orphaned_jobs()
    jobs = JobManager(launcher, history_store)
    app = FastAPI(
        title="Multi-pair FX Debate Test Console",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/api/health")
    def health() -> dict:
        state = readiness_probe()
        return {
            "ready": state.data_ready and state.llm_ready,
            "data": {"ready": state.data_ready, "source": state.data_source},
            "llm": {
                "ready": state.llm_ready,
                "provider": state.provider,
                "model": state.model,
            },
        }

    @app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    def start_run(request: RunRequest) -> dict:
        readiness = readiness_probe()
        if not (readiness.data_ready and readiness.llm_ready):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Excel/Database 数据源和 LLM 配置均就绪后才能启动真实 Debate。",
            )
        try:
            job = jobs.start(request)
        except ActiveRunError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return {
            "job_id": job.job_id,
            "conversation_id": job.conversation_id,
            "status": job.status,
            "poll_url": f"/api/runs/{job.job_id}",
        }

    @app.get("/api/conversations")
    def list_conversations() -> list[dict[str, Any]]:
        return history_store.list_conversations()

    @app.patch("/api/conversations/{conversation_id}")
    def rename_conversation(
        conversation_id: str, payload: dict[str, Any]
    ) -> dict[str, str]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="会话标题不能为空。")
        if not history_store.rename(conversation_id, title):
            raise HTTPException(status_code=404, detail="未找到该会话。")
        return {"status": "ok", "conversation_id": conversation_id, "title": title[:80]}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> dict[str, str]:
        try:
            deleted = jobs.delete_conversation(conversation_id)
        except ActiveRunError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="未找到该会话。")
        return {"status": "deleted", "conversation_id": conversation_id}

    @app.get("/api/settings")
    def get_local_settings() -> dict[str, Any]:
        return _local_settings_response()

    @app.put("/api/settings")
    def update_local_settings(payload: LocalSettingsRequest) -> dict[str, Any]:
        return _update_local_settings(payload)

    @app.get("/api/runs/{job_id}")
    def get_run(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="未找到该本地测试任务。",
            )
        return asdict(job)

    @app.get("/api/runs/{job_id}/events")
    def get_run_events(job_id: str, after: int = 0) -> dict:
        if after < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="after 必须大于或等于 0。",
            )
        events = jobs.events(job_id, after)
        if events is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="未找到该本地测试任务。",
            )
        return {
            "events": events,
            "next_after": after + len(events),
        }

    @app.get("/api/runs/{job_id}/diagnostics")
    def get_run_diagnostics(job_id: str) -> dict[str, Any]:
        """Return correlated, structured failures for the troubleshooting view."""
        events = jobs.events(job_id, 0)
        if events is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="未找到该本地测试任务。",
            )
        diagnostics = _build_diagnostics(events)
        return {"diagnostics": diagnostics, "count": len(diagnostics)}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_UI_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=_UI_DIR), name="fx-debate-static")
    return app


def _public_request(request: RunRequest) -> dict[str, Any]:
    """Keep the local console on the same three-variable upstream contract."""
    canonical = request.target.replace("/", "").replace("-", "")
    return {
        "target": request.target,
        "timeframe": (
            f"{request.horizon_count} {request.horizon_unit}; {request.timeframe}"
        ),
        "goal": request.goal
        or f"分析 {canonical} 未来走势，输出研究辅助性的方向判断、交易计划和失效条件。",
    }


def _conversation_title(request: RunRequest) -> str:
    goal = " ".join((request.goal or "").split())
    if goal:
        return goal[:80]
    return f"{request.target} · {request.horizon_count} {request.horizon_unit}"


def _run_options(request: RunRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id or f"req-ui-{uuid.uuid4().hex[:12]}",
        "as_of": request.as_of.isoformat() if request.as_of else None,
        "risk_profile": request.risk_profile,
        "language": "zh-CN",
    }


def _result_error(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "Debate 运行失败")
    return str(error or "Debate 运行失败")


def _safe_event_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return json.loads(
        json.dumps(redact_payload(value), ensure_ascii=False, default=str)
    )


def _parse_event_json(value: Any) -> Any:
    """Decode a tool payload while tolerating truncated/non-JSON output."""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _build_diagnostics(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Correlate worker failures with their nearest validator/tool evidence."""
    diagnostics: list[dict[str, Any]] = []
    validator_events: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if (
            event.get("type") != "tool_result"
            or data.get("tool") != "validate_fx_output"
        ):
            continue
        output = _parse_event_json(data.get("output"))
        if not isinstance(output, dict) or output.get("valid") is not False:
            continue
        key = (event.get("agent_id"), event.get("task_id"))
        validator_events.setdefault(key, []).append(event)

    failure_types = {
        "worker_failed",
        "worker_timeout",
        "worker_incomplete",
        "task_failed",
        "run_error",
        "run_recovered",
    }
    for event in events:
        event_type = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        message = str(data.get("error") or data.get("reason") or "")
        is_contract = data.get("error_kind") == "fx_validation_contract" or (
            "FX validation contract not met" in message
        )
        if event_type not in failure_types and not is_contract:
            continue
        if event_type in {"task_failed", "run_error"} and any(
            item["task_id"] == event.get("task_id") and item["message"] == message
            for item in diagnostics
        ):
            # The runtime emits a task-level failure after the worker has
            # already recorded the actionable contract failure. Keep one
            # diagnostic card while retaining the full event timeline.
            continue
        key = (event.get("agent_id"), event.get("task_id"))
        related = [
            item
            for item in validator_events.get(key, [])
            if int(item.get("sequence") or 0) <= int(event.get("sequence") or 0)
        ]
        related = related[-3:]
        validation = (
            data.get("validation") if isinstance(data.get("validation"), dict) else {}
        )
        if (not validation or not validation.get("errors")) and related:
            parsed = _parse_event_json(related[-1].get("data", {}).get("output"))
            if isinstance(parsed, dict):
                validation = {**parsed, **validation}
        errors = validation.get("errors") if isinstance(validation, dict) else []
        if not isinstance(errors, list):
            errors = []
        sequence = event.get("sequence")
        diagnostics.append(
            {
                "diagnostic_id": f"diag-{sequence}",
                "severity": "error",
                "title": "FX 契约校验失败" if is_contract else "运行失败",
                "message": message or "运行阶段未提供错误描述。",
                "error_kind": data.get("error_kind") or event_type,
                "phase": data.get("phase") or event_type,
                "agent_id": event.get("agent_id"),
                "task_id": event.get("task_id"),
                "sequence": sequence,
                "timestamp": event.get("timestamp"),
                "iteration": data.get("iterations") or data.get("iteration"),
                "validation_errors": errors[:20],
                "validation_error_count": (
                    validation.get("error_count", len(errors))
                    if isinstance(validation, dict)
                    else len(errors)
                ),
                "related_sequences": [item.get("sequence") for item in related],
                "retry_count": sum(
                    1
                    for item in events
                    if item.get("type") == "task_retry"
                    and item.get("agent_id") == event.get("agent_id")
                    and item.get("task_id") == event.get("task_id")
                    and int(item.get("sequence") or 0) <= int(sequence or 0)
                ),
            }
        )
    return diagnostics


def _load_local_env() -> None:
    load_dotenv(_AGENT_DIR / ".env", override=False)


def _local_env_values() -> dict[str, str]:
    path = _AGENT_DIR / ".env"
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].lstrip()
            key, separator, value = stripped.partition("=")
            if separator:
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_local_env(updates: dict[str, str]) -> None:
    path = _AGENT_DIR / ".env"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        stripped = line.strip()
        key = (
            stripped[7:].lstrip().split("=", 1)[0].strip()
            if stripped.startswith("export ")
            else stripped.split("=", 1)[0].strip()
        )
        if key in updates and "=" in stripped and not stripped.startswith("#"):
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _local_settings_response() -> dict[str, Any]:
    _load_local_env()
    values = _local_env_values()
    provider = values.get("LANGCHAIN_PROVIDER") or os.getenv(
        "LANGCHAIN_PROVIDER", "openai"
    )
    model = values.get("LANGCHAIN_MODEL_NAME") or os.getenv("LANGCHAIN_MODEL_NAME", "")
    base_url_key = (
        "DEEPSEEK_BASE_URL" if provider.lower() == "deepseek" else "OPENAI_BASE_URL"
    )
    api_key_key = (
        "DEEPSEEK_API_KEY" if provider.lower() == "deepseek" else "OPENAI_API_KEY"
    )
    synthetic_path = (
        _AGENT_DIR / "outputs" / "fx-debate-synthetic" / "complete_multi_pair.xlsx"
    )

    def effective_value(key: str) -> str:
        value = values.get(key) or os.getenv(key, "")
        if value.startswith("${") and value.endswith("}"):
            referenced_key = value[2:-1].strip()
            return values.get(referenced_key) or os.getenv(referenced_key, "")
        return value

    return {
        "llm": {
            "provider": provider,
            "model": model,
            "base_url": effective_value(base_url_key),
            "api_key_configured": bool(
                values.get(api_key_key) or os.getenv(api_key_key)
            ),
            "api_key_env": api_key_key,
            "reasoning_effort": effective_value("LANGCHAIN_REASONING_EFFORT") or "low",
        },
        "data": {
            "data_source": values.get(
                "FX_DEBATE_DATA_SOURCE", os.getenv("FX_DEBATE_DATA_SOURCE", "database")
            ),
            "excel_path": values.get(
                "FX_DEBATE_EXCEL_PATH", os.getenv("FX_DEBATE_EXCEL_PATH", "")
            ),
            "synthetic_path": str(synthetic_path) if synthetic_path.exists() else "",
            "database": {
                "host": values.get("MARKET_DB_HOST", os.getenv("MARKET_DB_HOST", "")),
                "port": int(
                    values.get("MARKET_DB_PORT", os.getenv("MARKET_DB_PORT", "15433"))
                    or 15433
                ),
                "name": values.get("MARKET_DB_NAME", os.getenv("MARKET_DB_NAME", "")),
                "user": values.get("MARKET_DB_USER", os.getenv("MARKET_DB_USER", "")),
                "password_configured": bool(
                    values.get("MARKET_DB_PASSWORD") or os.getenv("MARKET_DB_PASSWORD")
                ),
            },
        },
    }


def _update_local_settings(payload: LocalSettingsRequest) -> dict[str, Any]:
    current = _local_env_values()
    updates: dict[str, str] = {}
    if payload.provider is not None:
        updates["LANGCHAIN_PROVIDER"] = payload.provider.strip().lower()
    if payload.model is not None:
        updates["LANGCHAIN_MODEL_NAME"] = payload.model.strip()
    provider = updates.get(
        "LANGCHAIN_PROVIDER", current.get("LANGCHAIN_PROVIDER", "openai")
    ).lower()
    if payload.base_url is not None:
        updates[
            "DEEPSEEK_BASE_URL" if provider == "deepseek" else "OPENAI_BASE_URL"
        ] = payload.base_url.strip()
    if payload.api_key:
        updates["DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"] = (
            payload.api_key.strip()
        )
    if payload.reasoning_effort is not None:
        updates["LANGCHAIN_REASONING_EFFORT"] = payload.reasoning_effort
    for field, env_name in (
        ("data_source", "FX_DEBATE_DATA_SOURCE"),
        ("excel_path", "FX_DEBATE_EXCEL_PATH"),
        ("db_host", "MARKET_DB_HOST"),
        ("db_name", "MARKET_DB_NAME"),
        ("db_user", "MARKET_DB_USER"),
        ("db_password", "MARKET_DB_PASSWORD"),
    ):
        value = getattr(payload, field)
        if value is not None:
            updates[env_name] = str(value)
    if payload.db_port is not None:
        updates["MARKET_DB_PORT"] = str(payload.db_port)
    if payload.data_source is not None:
        updates["MARKET_DB_ENABLED"] = "1" if payload.data_source == "database" else "0"
    if not updates:
        return _local_settings_response()
    _write_local_env(updates)
    os.environ.update(updates)
    return _local_settings_response()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


app = create_app()
