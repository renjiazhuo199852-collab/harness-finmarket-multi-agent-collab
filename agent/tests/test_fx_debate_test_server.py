"""独立 FX Debate 测试前端后端的公开 HTTP 契约。"""

from __future__ import annotations

import json
import time
from threading import Event

from fastapi.testclient import TestClient

import fx_debate_test_server as server
from fx_debate_test_server import (
    JobRecord,
    HistoryStore,
    Readiness,
    _build_diagnostics,
    create_app,
)


def test_health_reports_data_source_and_llm_readiness_without_secrets() -> None:
    app = create_app(
        readiness_probe=lambda: Readiness(
            data_ready=True,
            data_source="excel",
            llm_ready=False,
            provider="",
            model="",
        )
    )

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "ready": False,
        "data": {"ready": True, "source": "excel"},
        "llm": {"ready": False, "provider": "", "model": ""},
    }


def test_local_settings_round_trip_redacts_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "_AGENT_DIR", tmp_path)
    app = create_app()
    client = TestClient(app)

    saved = client.put(
        "/api/settings",
        json={
            "provider": "deepseek",
            "model": "gpt-5.6-terra",
            "base_url": "https://example.test/v1",
            "api_key": "secret-value",
            "reasoning_effort": "low",
            "data_source": "excel",
            "excel_path": "/tmp/complete.xlsx",
        },
    )

    assert saved.status_code == 200
    payload = saved.json()
    assert payload["llm"]["provider"] == "deepseek"
    assert payload["llm"]["api_key_configured"] is True
    assert "secret-value" not in saved.text
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=secret-value" in env_text
    assert client.get("/api/settings").json()["data"]["data_source"] == "excel"


def test_server_restart_recovers_orphaned_running_job(tmp_path) -> None:
    history = HistoryStore(tmp_path / "history")
    history.save_job(
        JobRecord(
            job_id="fxui-orphan",
            conversation_id="fxconv-orphan",
            status="running",
            created_at="2026-08-13T03:34:05Z",
            started_at="2026-08-13T03:34:05Z",
        )
    )

    create_app(history=history)

    recovered = history.get_job("fxui-orphan")
    assert recovered is not None
    assert recovered.status == "failed"
    assert "服务已重启" in (recovered.error or "")


def test_user_can_start_and_poll_a_real_debate_job_contract() -> None:
    def launcher(public_request: dict, run_options: dict, emit) -> dict:
        del emit
        assert public_request["target"] == "EUR/USD"
        assert public_request["timeframe"] == "2 weeks; 4H/1D"
        assert "EURUSD" in public_request["goal"]
        assert run_options["risk_profile"] == "balanced"
        return {
            "ok": True,
            "status": "completed",
            "run_id": "swarm-ui-test",
            "decision": {"decision": "wait", "confidence": 0.58},
            "report_markdown": "# EUR/USD 外汇 Debate 结论",
        }

    app = create_app(
        readiness_probe=lambda: Readiness(
            True, "excel", True, "deepseek", "test-model"
        ),
        launcher=launcher,
    )
    client = TestClient(app)

    started = client.post(
        "/api/runs",
        json={
            "horizon_count": 2,
            "horizon_unit": "weeks",
            "timeframe": "4H/1D",
            "risk_profile": "balanced",
            "request_id": "req-ui-test",
            "confirm_cost": True,
        },
    )

    assert started.status_code == 202
    job_id = started.json()["job_id"]
    for _ in range(50):
        status = client.get(f"/api/runs/{job_id}")
        if status.json()["status"] == "completed":
            break
        time.sleep(0.01)

    assert status.status_code == 200
    assert status.json()["result"]["run_id"] == "swarm-ui-test"
    assert status.json()["result"]["decision"] == {
        "decision": "wait",
        "confidence": 0.58,
    }


def test_root_serves_the_independent_eurusd_test_console() -> None:
    app = create_app(
        readiness_probe=lambda: Readiness(True, "excel", True, "deepseek", "test-model")
    )

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "EUR/USD 五 Agent 调试控制台" in response.text
    assert 'id="run-form"' in response.text
    assert 'id="event-feed"' in response.text
    assert 'id="event-detail"' in response.text
    assert 'id="active-calls"' in response.text
    assert 'id="agent-list"' in response.text
    assert 'id="conversation-list"' in response.text
    assert 'id="debate-canvas"' in response.text
    assert 'id="data-view"' in response.text
    assert 'id="chat-form"' in response.text
    assert 'id="chat-thread"' in response.text
    assert 'id="chat-view"' in response.text
    assert 'data-view="logs"' in response.text
    assert 'data-view="settings"' in response.text
    assert 'id="detail-output-view"' in response.text
    assert 'data-resize="left"' in response.text
    assert 'data-resize="right"' in response.text
    assert 'data-resize="center"' in response.text
    assert 'id="report-view"' in response.text
    assert 'id="data-preview-table-wrap"' in response.text
    assert 'id="data-preview-domain"' in response.text
    assert 'id="data-preview-json"' in response.text
    assert "styles.css?v=20260817-01" in response.text
    assert "/static/app.js" in response.text


def test_second_paid_run_is_rejected_while_first_is_active() -> None:
    release = Event()

    def launcher(public_request: dict, run_options: dict, emit) -> dict:
        del public_request, run_options, emit
        release.wait(timeout=2)
        return {"ok": True, "status": "completed"}

    app = create_app(
        readiness_probe=lambda: Readiness(
            True, "excel", True, "deepseek", "test-model"
        ),
        launcher=launcher,
    )
    client = TestClient(app)
    payload = {
        "horizon_count": 2,
        "horizon_unit": "weeks",
        "timeframe": "4H/1D",
        "risk_profile": "balanced",
        "confirm_cost": True,
    }

    first = client.post("/api/runs", json=payload)
    second = client.post("/api/runs", json=payload)
    release.set()

    assert first.status_code == 202
    assert second.status_code == 409
    assert "正在运行" in second.json()["detail"]


def test_conversation_history_survives_manager_reload(tmp_path) -> None:
    history = HistoryStore(tmp_path / "history")

    def launcher(public_request: dict, run_options: dict, emit) -> dict:
        del run_options
        emit(
            {
                "type": "worker_started",
                "agent_id": "pair_bull",
                "data": {"input": public_request},
            }
        )
        return {
            "ok": True,
            "status": "completed",
            "run_id": "swarm-history-test",
            "decision": {"decision": "wait", "confidence": 0.7},
        }

    app = create_app(
        readiness_probe=lambda: Readiness(
            True, "excel", True, "deepseek", "test-model"
        ),
        launcher=launcher,
        history=history,
    )
    client = TestClient(app)
    started = client.post(
        "/api/runs",
        json={
            "horizon_count": 2,
            "horizon_unit": "weeks",
            "timeframe": "4H/1D",
            "risk_profile": "balanced",
            "conversation_id": "fxconv-regression",
            "goal": "检查历史回放",
            "confirm_cost": True,
        },
    )
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    for _ in range(50):
        job = client.get(f"/api/runs/{job_id}").json()
        if job["status"] == "completed":
            break
        time.sleep(0.01)

    conversations = client.get("/api/conversations").json()
    assert conversations[0]["conversation_id"] == "fxconv-regression"
    assert conversations[0]["title"] == "检查历史回放"
    assert conversations[0]["last_job_id"] == job_id
    assert client.get(f"/api/runs/{job_id}/events?after=0").json()["next_after"] == 1

    assert (
        client.patch(
            "/api/conversations/fxconv-regression", json={"title": "EUR/USD 历史回放"}
        ).status_code
        == 200
    )
    assert history.list_conversations()[0]["title"] == "EUR/USD 历史回放"

    deleted = client.delete("/api/conversations/fxconv-regression")
    assert deleted.status_code == 200
    assert deleted.json() == {
        "status": "deleted",
        "conversation_id": "fxconv-regression",
    }
    assert client.get("/api/conversations").json() == []
    assert history.get_job(job_id) is None
    assert history.get_events(job_id) is None
    assert client.get(f"/api/runs/{job_id}").status_code == 404
    assert client.delete("/api/conversations/fxconv-regression").status_code == 404


def test_user_can_poll_detailed_incremental_execution_events() -> None:
    def launcher(public_request: dict, run_options: dict, emit) -> dict:
        del public_request, run_options
        emit(
            {
                "type": "agent_started",
                "agent_id": "pair_bull",
                "task_id": "task-pair-bull",
                "data": {
                    "input": {
                        "system_prompt": "只使用内部证据",
                        "user_prompt": "分析 EURUSD",
                        "api_key": "must-not-reach-browser",
                    }
                },
            }
        )
        emit(
            {
                "type": "database_query_completed",
                "agent_id": "pair_bull",
                "task_id": "task-pair-bull",
                "data": {
                    "call_id": "db-1",
                    "operation": "public.market_bars",
                    "output": {"row_count": 120},
                    "elapsed_ms": 17,
                },
            }
        )
        return {"ok": True, "status": "completed", "run_id": "swarm-observed"}

    app = create_app(
        readiness_probe=lambda: Readiness(
            True, "excel", True, "deepseek", "test-model"
        ),
        launcher=launcher,
    )
    client = TestClient(app)
    started = client.post(
        "/api/runs",
        json={
            "horizon_count": 2,
            "horizon_unit": "weeks",
            "timeframe": "4H/1D",
            "risk_profile": "balanced",
            "confirm_cost": True,
        },
    )
    job_id = started.json()["job_id"]

    for _ in range(50):
        response = client.get(f"/api/runs/{job_id}/events?after=0")
        if response.status_code == 200 and response.json()["next_after"] >= 2:
            break
        time.sleep(0.01)

    payload = response.json()
    assert [event["type"] for event in payload["events"]] == [
        "agent_started",
        "database_query_completed",
    ]
    assert payload["events"][0]["sequence"] == 1
    assert payload["events"][0]["data"]["input"]["api_key"] == "[redacted]"
    assert payload["events"][1]["data"]["output"]["row_count"] == 120
    assert payload["next_after"] == 2

    incremental = client.get(f"/api/runs/{job_id}/events?after=1")
    assert [event["sequence"] for event in incremental.json()["events"]] == [2]


def test_diagnostics_correlate_contract_failure_with_validator_event() -> None:
    events = [
        {
            "sequence": 1,
            "timestamp": "2026-08-14T01:00:00Z",
            "type": "tool_result",
            "agent_id": "pair_bull",
            "task_id": "task-pair-bull",
            "data": {
                "tool": "validate_fx_output",
                "status": "ok",
                "output": json.dumps(
                    {
                        "valid": False,
                        "mode": "hypothesis",
                        "errors": [
                            {
                                "code": "SCHEMA_VALIDATION_ERROR",
                                "path": "$.causal_chains[1].claim_id",
                                "message": "claim_id values must be unique",
                            }
                        ],
                    }
                ),
            },
        },
        {
            "sequence": 2,
            "timestamp": "2026-08-14T01:00:01Z",
            "type": "worker_failed",
            "agent_id": "pair_bull",
            "task_id": "task-pair-bull",
            "data": {
                "error": "FX validation contract not met: validate_fx_output never returned valid=true",
                "error_kind": "fx_validation_contract",
                "phase": "contract_validation",
                "validation": {"error_count": 1},
            },
        },
    ]

    diagnostics = _build_diagnostics(events)

    assert len(diagnostics) == 1
    assert diagnostics[0]["title"] == "FX 契约校验失败"
    assert diagnostics[0]["related_sequences"] == [1]
    assert (
        diagnostics[0]["validation_errors"][0]["path"] == "$.causal_chains[1].claim_id"
    )
