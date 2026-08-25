"""Swarm HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_swarm_routes(app, ...)``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_swarm_runtime = None


def _public_evidence_bundle(run: Any) -> dict[str, Any] | None:
    """Return the durable FX evidence bundle without exposing runtime context.

    Evidence is frozen on the run record so historical data views remain
    reproducible after the Session SSE connection is gone.  ``trusted_context``
    may also contain resolved-request internals, therefore only the parsed
    evidence bundle is projected through the public endpoint.
    """
    trusted_context = getattr(run, "trusted_context", None)
    if not isinstance(trusted_context, dict):
        return None
    raw_bundle = trusted_context.get("evidence_bundle_json")
    if not isinstance(raw_bundle, str) or not raw_bundle.strip():
        return None
    try:
        bundle = json.loads(raw_bundle)
    except (TypeError, json.JSONDecodeError):
        return None
    return bundle if isinstance(bundle, dict) else None


def _get_swarm_runtime():
    """Lazy-init SwarmRuntime singleton."""
    global _swarm_runtime
    if _swarm_runtime is not None:
        return _swarm_runtime
    from src.config import load_swarm_agent_config
    from src.swarm.store import SwarmStore
    from src.swarm.runtime import SwarmRuntime

    # Adjust path: this file is at agent/src/api/, so parent.parent.parent = agent/
    swarm_dir = Path(__file__).resolve().parent.parent.parent / ".swarm" / "runs"
    store = SwarmStore(base_dir=swarm_dir)
    # Boot-time / operator-trusted: REST API callers cannot influence the
    # config path. See docs/2026-05-25_swarm_mcp_tools_roadmap.md.
    agent_config = load_swarm_agent_config()
    _swarm_runtime = SwarmRuntime(store=store, agent_config=agent_config)
    return _swarm_runtime


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AuthDep = Callable[..., Awaitable[Any] | Any]


class UpdateFinalReportRequest(BaseModel):
    """User-authored replacement for the readable final report draft."""

    markdown: str = Field(min_length=1, max_length=500_000)


def register_swarm_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
    require_event_stream_auth: AuthDep | None = None,
) -> None:
    """Mount the swarm routes onto ``app``.

    Resolves ``require_auth`` and ``require_event_stream_auth`` from the host
    ``api_server`` module via ``sys.modules`` when not passed explicitly.
    """
    import sys as _sys

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    if host is None:
        raise RuntimeError(
            "register_swarm_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )

    if require_auth is None:
        require_auth = host.require_auth
    if require_event_stream_auth is None:
        require_event_stream_auth = host.require_event_stream_auth
    require_settings_write_auth = host.require_settings_write_auth

    def _host_validate_path_param(value: str, kind: str) -> None:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        h._validate_path_param(value, kind)

    def _host_shell_tools_enabled_for_request(request: Request) -> bool:
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        return h._shell_tools_enabled_for_request(request)

    # --- Routes ---

    @app.get("/swarm/presets", dependencies=[Depends(require_auth)])
    async def list_swarm_presets():
        """List Swarm YAML presets."""
        from src.swarm.presets import list_presets

        return list_presets()

    @app.get("/swarm/presets/{preset_name}", dependencies=[Depends(require_auth)])
    async def get_swarm_preset(preset_name: str):
        """Return read-only metadata for a Swarm YAML preset."""
        _host_validate_path_param(preset_name, "preset_name")
        from src.swarm.presets import PRESETS_DIR, USER_PRESETS_DIR, inspect_preset, resolve_preset_path

        try:
            path = resolve_preset_path(preset_name)
            if path is None:
                raise FileNotFoundError(f"Preset {preset_name!r} not found")
            detail = inspect_preset(preset_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        source = "user" if path.parent == USER_PRESETS_DIR else "bundled"
        return {
            **detail,
            "source": source,
            "file": path.name if path.parent == PRESETS_DIR else None,
        }

    def _agent_path(preset_name: str, agent_id: str) -> None:
        _host_validate_path_param(preset_name, "preset_name")
        _host_validate_path_param(agent_id, "agent_id")

    def _customization():
        from src.swarm.customization import get_customization_service

        return get_customization_service()

    @app.get(
        "/swarm/presets/{preset_name}/agents/{agent_id}/editor",
        dependencies=[Depends(require_auth)],
    )
    async def get_agent_editor(preset_name: str, agent_id: str):
        """Return default/effective prompt and skill configuration."""
        _agent_path(preset_name, agent_id)
        from src.swarm.customization import CustomizationError

        try:
            return _customization().editor_payload(preset_name, agent_id)
        except CustomizationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/swarm/presets/{preset_name}/agents/{agent_id}/proposals",
        dependencies=[Depends(require_settings_write_auth)],
    )
    async def propose_agent_edit(preset_name: str, agent_id: str, payload: dict[str, Any]):
        """Generate and review a candidate without writing it to disk."""
        _agent_path(preset_name, agent_id)
        from src.swarm.customization import CustomizationError, RevisionConflict

        try:
            proposal = await run_in_threadpool(
                _customization().propose,
                preset_name,
                agent_id,
                str(payload.get("instruction", "")),
                str(payload.get("base_revision", "")),
                str(payload.get("session_id")) if payload.get("session_id") else None,
            )
            return proposal.model_dump()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CustomizationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # provider errors are intentionally generic at this boundary
            raise HTTPException(status_code=502, detail=f"agent edit proposal failed: {exc}") from exc

    @app.post(
        "/swarm/presets/{preset_name}/agents/{agent_id}/proposals/{proposal_id}/apply",
        dependencies=[Depends(require_settings_write_auth)],
    )
    async def apply_agent_edit(preset_name: str, agent_id: str, proposal_id: str, payload: dict[str, Any]):
        """Apply an approved proposal using optimistic revision checking."""
        _agent_path(preset_name, agent_id)
        from src.swarm.customization import CustomizationError, RevisionConflict

        proposal = _customization().proposal(proposal_id)
        if proposal is None or proposal.preset_name != preset_name or proposal.agent_id != agent_id:
            raise HTTPException(status_code=404, detail=f"proposal {proposal_id} not found")
        try:
            return _customization().apply(proposal_id, str(payload.get("base_revision", "")))
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/swarm/presets/{preset_name}/agents/{agent_id}/proposals/{proposal_id}/revise",
        dependencies=[Depends(require_settings_write_auth)],
    )
    async def revise_agent_edit(preset_name: str, agent_id: str, proposal_id: str, payload: dict[str, Any]):
        """Re-review a user-edited candidate without persisting it."""
        _agent_path(preset_name, agent_id)
        from src.swarm.customization import AgentCandidate, CustomizationError, RevisionConflict

        proposal = _customization().proposal(proposal_id)
        if proposal is None or proposal.preset_name != preset_name or proposal.agent_id != agent_id:
            raise HTTPException(status_code=404, detail=f"proposal {proposal_id} not found")
        try:
            candidate = AgentCandidate.model_validate(payload.get("candidate", {}))
            revised = await run_in_threadpool(
                _customization().revise_proposal,
                proposal_id,
                str(payload.get("base_revision", "")),
                candidate,
            )
            return revised.model_dump()
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (CustomizationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CustomizationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/swarm/presets/{preset_name}/agents/{agent_id}/reset",
        dependencies=[Depends(require_settings_write_auth)],
    )
    async def reset_agent_edit(preset_name: str, agent_id: str, payload: dict[str, Any]):
        """Restore one agent to the bundled/user preset defaults."""
        _agent_path(preset_name, agent_id)
        from src.swarm.customization import CustomizationError, RevisionConflict

        try:
            return _customization().reset(preset_name, agent_id, str(payload.get("base_revision", "")))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CustomizationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/swarm/presets/{preset_name}/agents/{agent_id}/history",
        dependencies=[Depends(require_auth)],
    )
    async def get_agent_edit_history(preset_name: str, agent_id: str):
        """Return the bounded append-only history for one agent."""
        _agent_path(preset_name, agent_id)
        try:
            return {"preset_name": preset_name, "agent_id": agent_id, "entries": _customization().history(preset_name, agent_id)}
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/swarm/presets/{preset_name}/reload",
        dependencies=[Depends(require_settings_write_auth)],
    )
    async def reload_swarm_preset(preset_name: str):
        """Validate overrides and reload effective config for future runs."""
        _host_validate_path_param(preset_name, "preset_name")
        from src.swarm.customization import CustomizationError

        try:
            return _customization().reload(preset_name)
        except (ValueError, FileNotFoundError, CustomizationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/swarm/runs", dependencies=[Depends(require_auth)])
    async def create_swarm_run(payload: dict, http_request: Request):
        """Start a swarm run: body must include preset_name and user_vars."""
        runtime = _get_swarm_runtime()
        preset_name = payload.get("preset_name", "")
        user_vars = payload.get("user_vars", {})
        try:
            run = runtime.start_run(
                preset_name,
                user_vars,
                include_shell_tools=_host_shell_tools_enabled_for_request(http_request),
            )
            return {"id": run.id, "status": run.status.value, "preset_name": run.preset_name}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/swarm/runs", dependencies=[Depends(require_auth)])
    async def list_swarm_runs(limit: int = Query(20, ge=1, le=100)):
        """List swarm runs (newest first), reconciled."""
        runtime = _get_swarm_runtime()
        runs = runtime._store.list_runs(limit=limit)
        items = []
        for r in runs:
            # Reconcile each row: a zombie running run will be auto-finalized so
            # the dashboard never shows a "running" stuck row.
            reconciled = runtime._store.reconcile_run(r, write=True)
            items.append(
                {
                    "id": reconciled.id,
                    "preset_name": reconciled.preset_name,
                    "status": reconciled.status.value,
                    "is_stale": runtime._store.is_run_stale(reconciled),
                    "created_at": reconciled.created_at,
                    "completed_at": reconciled.completed_at,
                    "task_count": len(reconciled.tasks),
                    "completed_count": sum(
                        1 for t in reconciled.tasks if t.status.value == "completed"
                    ),
                }
            )
        return items

    @app.get("/swarm/runs/{run_id}", dependencies=[Depends(require_auth)])
    async def get_swarm_run(run_id: str):
        """Swarm run detail including task statuses and persisted events."""
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        loaded = runtime._store.load_run(run_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        run = runtime._store.reconcile_run(loaded, write=True)
        # The session EventBus is ephemeral. Include the durable run log so
        # history views can reconstruct the full trace after a page reload.
        events = [event.model_dump() for event in runtime._store.read_events(run.id)]

        from src.swarm.serialization import serialize_task

        return {
            "id": run.id,
            "preset_name": run.preset_name,
            "status": run.status.value,
            "is_stale": runtime._store.is_run_stale(run),
            "user_vars": run.user_vars,
            "agents": [a.model_dump() for a in run.agents],
            "tasks": [
                {
                    **serialize_task(t),
                    # Keep the existing REST field while sharing the public
                    # serializer used by the other swarm read paths.
                    "worker_iterations": getattr(t, "worker_iterations", 0),
                }
                for t in run.tasks
            ],
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "final_report": run.final_report,
            "events": events,
            "evidence_bundle": _public_evidence_bundle(run),
        }

    @app.put("/swarm/runs/{run_id}/report", dependencies=[Depends(require_auth)])
    async def update_swarm_report(run_id: str, payload: UpdateFinalReportRequest):
        """Persist a user-edited final report without changing run execution state."""
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        run = runtime._store.load_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        run.final_report = payload.markdown
        runtime._store.update_run(run)
        return {"id": run.id, "final_report": run.final_report, "updated": True}

    @app.get(
        "/swarm/runs/{run_id}/events",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def swarm_run_events(
        run_id: str,
        request: Request,
        last_index: int = Query(0, ge=0),
        last_event_id: int | None = Header(None, alias="Last-Event-ID", ge=0),
    ):
        """SSE stream for a swarm run."""
        import asyncio

        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        if not runtime._store.load_run(run_id):
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        async def event_stream():
            # Browser EventSource reconnects with Last-Event-ID. Keep the
            # existing query parameter for non-browser and older clients.
            idx = last_event_id if last_event_id is not None else last_index
            while True:
                if await request.is_disconnected():
                    break
                events = runtime._store.read_events(run_id, after_index=idx)
                for evt in events:
                    idx += 1
                    yield f"id: {idx}\nevent: {evt.type}\ndata: {json.dumps(evt.model_dump(), ensure_ascii=False)}\n\n"
                run = runtime._store.load_run(run_id)
                if not run:
                    yield 'event: done\ndata: {"status": "missing"}\n\n'
                    break
                # Reconcile so a zombie running run can still close this SSE
                # stream cleanly — without it, a dead host would keep the
                # stream open forever and block the dashboard's "done" state.
                reconciled = runtime._store.reconcile_run(run, write=True)
                if reconciled.status.value in ("completed", "failed", "cancelled"):
                    yield f'event: done\ndata: {{"status": "{reconciled.status.value}"}}\n\n'
                    break
                await asyncio.sleep(2)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/swarm/runs/{run_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_swarm_run(run_id: str):
        """Cancel an active swarm run."""
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        ok = runtime.cancel_run(run_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"No active run {run_id}")
        return {"status": "cancelled"}

    @app.post("/swarm/runs/{run_id}/retry", dependencies=[Depends(require_auth)])
    async def retry_swarm_run(run_id: str, http_request: Request):
        """Retry a failed, stale, or cancelled swarm run.

        Creates a new run with the same preset and user_vars as the original.
        """
        _host_validate_path_param(run_id, "run_id")
        runtime = _get_swarm_runtime()
        loaded = runtime._store.load_run(run_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        # Reconcile first so a stale "running" run whose host died gets demoted
        # before we gate on status; only a genuinely active run blocks retry.
        from src.swarm.models import RunStatus

        reconciled = runtime._store.reconcile_run(loaded, write=True)
        if reconciled.status == RunStatus.running:
            raise HTTPException(
                status_code=409, detail="Cannot retry a running run. Cancel it first."
            )

        try:
            new_run = runtime.start_run(
                reconciled.preset_name,
                reconciled.user_vars or {},
                include_shell_tools=_host_shell_tools_enabled_for_request(http_request),
            )
            return {
                "id": new_run.id,
                "status": new_run.status.value,
                "preset_name": new_run.preset_name,
            }
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
