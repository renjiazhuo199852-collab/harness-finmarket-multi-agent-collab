"""Public swarm REST and SSE contract regressions."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api_server
import src.api.swarm_routes as swarm_routes
import src.swarm.customization as customization
from src.swarm.models import RunStatus, SwarmEvent, SwarmRun, SwarmTask
from src.swarm.store import SwarmStore


@pytest.fixture
def swarm_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SwarmStore:
    """Route the process-wide swarm endpoint at an isolated on-disk store."""
    store = SwarmStore(base_dir=tmp_path / "runs")
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.setattr(swarm_routes, "_swarm_runtime", SimpleNamespace(_store=store))
    return store


def _client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _create_run(
    store: SwarmStore,
    *,
    run_id: str = "run-contract",
    status: RunStatus = RunStatus.pending,
    tasks: list[SwarmTask] | None = None,
) -> SwarmRun:
    run = SwarmRun(
        id=run_id,
        preset_name="contract-test",
        status=status,
        created_at="2026-07-16T00:00:00+00:00",
        completed_at=(
            "2026-07-16T00:00:01+00:00" if status == RunStatus.completed else None
        ),
        tasks=tasks or [],
    )
    store.create_run(run)
    return run


def test_swarm_events_returns_404_before_streaming_missing_run(
    swarm_store: SwarmStore,
) -> None:
    response = _client().get("/swarm/runs/missing-run/events")

    assert response.status_code == 404
    assert response.json()["detail"] == "Run missing-run not found"


def test_swarm_events_resumes_from_last_event_id_header(
    swarm_store: SwarmStore,
) -> None:
    run = _create_run(swarm_store, status=RunStatus.completed)
    for index in range(1, 4):
        swarm_store.append_event(
            run.id,
            SwarmEvent(
                type=f"step_{index}",
                data={"index": index},
                timestamp=f"2026-07-16T00:00:0{index}+00:00",
            ),
        )

    response = _client().get(
        f"/swarm/runs/{run.id}/events?last_index=1",
        headers={"Last-Event-ID": "2"},
    )

    assert response.status_code == 200
    assert "event: step_1" not in response.text
    assert "event: step_2" not in response.text
    assert "id: 3\nevent: step_3" in response.text
    assert 'event: done\ndata: {"status": "completed"}' in response.text


def test_swarm_events_keeps_last_index_query_compatibility(
    swarm_store: SwarmStore,
) -> None:
    run = _create_run(swarm_store, status=RunStatus.completed)
    for index in range(1, 3):
        swarm_store.append_event(
            run.id,
            SwarmEvent(
                type=f"query_step_{index}",
                timestamp=f"2026-07-16T00:00:0{index}+00:00",
            ),
        )

    response = _client().get(f"/swarm/runs/{run.id}/events?last_index=1")

    assert response.status_code == 200
    assert "event: query_step_1" not in response.text
    assert "id: 2\nevent: query_step_2" in response.text


def test_swarm_detail_uses_redacted_public_task_projection(
    swarm_store: SwarmStore,
) -> None:
    internal_path = str(
        Path.cwd() / "agent" / ".swarm" / "runs" / "secret" / "task.log"
    )
    _create_run(
        swarm_store,
        tasks=[
            SwarmTask(
                id="task-1",
                agent_id="analyst",
                prompt_template="internal prompt",
                summary="Public summary",
                artifacts=[internal_path],
                error=f"failed while reading {internal_path}",
                worker_iterations=3,
            )
        ],
    )

    response = _client().get("/swarm/runs/run-contract")

    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["summary"] == "Public summary"
    assert "<redacted>" in task["error"]
    assert internal_path not in response.text
    assert "artifacts" not in task
    assert "prompt_template" not in task
    assert task["worker_iterations"] == 3
    assert task["iterations"] == 3


def test_swarm_detail_includes_persisted_events_for_history_replay(
    swarm_store: SwarmStore,
) -> None:
    run = _create_run(swarm_store, status=RunStatus.completed)
    swarm_store.append_event(
        run.id,
        SwarmEvent(
            type="tool_call",
            data={"tool": "get_fx_evidence_manifest"},
            timestamp="2026-07-16T00:00:01+00:00",
        ),
    )

    response = _client().get(f"/swarm/runs/{run.id}")

    assert response.status_code == 200
    assert response.json()["events"][0]["type"] == "tool_call"


def test_swarm_report_update_persists_user_edited_final_draft(
    swarm_store: SwarmStore,
) -> None:
    run = _create_run(swarm_store, status=RunStatus.completed)
    run.final_report = "# 原始终稿"
    swarm_store.update_run(run)

    response = _client().put(
        f"/swarm/runs/{run.id}/report",
        json={"markdown": "# 用户修订后的终稿\n\n结论：等待确认"},
    )

    assert response.status_code == 200
    assert response.json()["updated"] is True
    assert swarm_store.load_run(run.id).final_report == "# 用户修订后的终稿\n\n结论：等待确认"


def test_swarm_detail_projects_persisted_evidence_bundle_for_history_data_view(
    swarm_store: SwarmStore,
) -> None:
    run = _create_run(swarm_store, status=RunStatus.completed)
    run.trusted_context = {
        "evidence_bundle_json": json.dumps(
            {
                "evidence_context_id": "ctx-history",
                "as_of": "2026-07-16T00:00:00+00:00",
                "source_name": "database",
                "evidence": [
                    {
                        "evidence_id": "quote-1",
                        "domain": "market",
                        "name": "EURUSD spot",
                        "source": "database",
                    }
                ],
            }
        ),
        "resolved_request_json": "internal request details",
    }
    swarm_store.update_run(run)

    response = _client().get(f"/swarm/runs/{run.id}")

    assert response.status_code == 200
    assert response.json()["evidence_bundle"]["evidence_context_id"] == "ctx-history"
    assert response.json()["evidence_bundle"]["evidence"][0]["evidence_id"] == "quote-1"
    assert "resolved_request_json" not in response.text


def test_swarm_presets_require_auth_when_api_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_server, "_API_KEY", "acceptance-token")

    assert _client().get("/swarm/presets").status_code == 401
    assert _client().get("/swarm/presets/fx_debate_team").status_code == 401


def test_swarm_presets_metadata_is_public_projection_with_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_server, "_API_KEY", "acceptance-token")
    headers = {"Authorization": "Bearer acceptance-token"}

    list_response = _client().get("/swarm/presets", headers=headers)
    detail_response = _client().get("/swarm/presets/fx_debate_team", headers=headers)

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["name"] == "fx_debate_team"
    assert detail["agents"]
    assert detail["tasks"]
    assert detail["layers"]
    assert "system_prompt" not in detail_response.text
    assert "prompt_template" not in detail_response.text


def test_agent_editor_routes_keep_proposals_review_first(
    swarm_store: SwarmStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = customization.AgentCandidate(system_prompt="Candidate", skills=[], skill_overrides={})
    proposal = customization.AgentEditProposal(
        proposal_id="proposal-route",
        preset_name="fx_debate_team",
        agent_id="pair_bull",
        instruction="加强审查",
        base_revision="base-revision",
        candidate=candidate,
        diff={"prompt": "diff", "skills_added": [], "skills_removed": [], "skills_modified": []},
        review=customization.ProposalReview(approved=True, risk_level="low", checks=["safe"]),
        created_at="2026-07-16T00:00:00+00:00",
    )

    class FakeCustomization:
        def editor_payload(self, _preset: str, _agent: str):
            return {"preset_name": "fx_debate_team", "agent_id": "pair_bull", "revision": "base-revision"}

        def propose(self, *_args, **_kwargs):
            return proposal

        def proposal(self, _proposal_id: str):
            return proposal

        def apply(self, *_args, **_kwargs):
            return {"source": "user_override", "revision": "new-revision"}

        def reset(self, *_args, **_kwargs):
            return {"source": "default", "revision": "default-revision"}

        def history(self, *_args, **_kwargs):
            return [{"action": "apply", "revision": "new-revision"}]

        def reload(self, _preset: str):
            return {"preset_name": "fx_debate_team", "valid": True, "errors": [], "warnings": [], "affects": "new_runs_only"}

    monkeypatch.setattr(customization, "get_customization_service", lambda: FakeCustomization())
    client = _client()

    assert client.get("/swarm/presets/fx_debate_team/agents/pair_bull/editor").status_code == 200
    proposal_response = client.post(
        "/swarm/presets/fx_debate_team/agents/pair_bull/proposals",
        json={"instruction": "加强审查", "base_revision": "base-revision"},
    )
    assert proposal_response.status_code == 200
    assert proposal_response.json()["proposal_id"] == "proposal-route"
    apply_response = client.post(
        "/swarm/presets/fx_debate_team/agents/pair_bull/proposals/proposal-route/apply",
        json={"base_revision": "base-revision"},
    )
    assert apply_response.status_code == 200
    assert client.post("/swarm/presets/fx_debate_team/agents/pair_bull/reset", json={"base_revision": "base-revision"}).status_code == 200
    assert client.get("/swarm/presets/fx_debate_team/agents/pair_bull/history").json()["entries"]
    assert client.post("/swarm/presets/fx_debate_team/reload", json={}).json()["valid"] is True
