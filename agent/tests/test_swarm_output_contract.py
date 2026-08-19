"""Regression tests for P01 + P03 — swarm output contract.

A worker that produced no substantive deliverable (plan-only stub, mock
data, unparsed tool markup, raw tool envelope, or a data agent that made no
tool call and wrote no report) must NOT be reported ``completed``, and the
runtime must not fold ``timeout`` / ``token_limit`` / ``incomplete`` into a
successful run.

The ``test_timeout_terminal_*`` runtime test is the fail-before / pass-after
anchor: on the pre-fix code ``timeout`` was mapped to ``completed`` so the run
reported success; post-fix it is a failure. The content-contract unit tests
pin the new ``_classify_deliverable`` policy (Hybrid: content-sanity for all
agents, tool-evidence only for data agents — tool-less synthesis/editor roles
are intentionally NOT failed).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.agent.tools import BaseTool, ToolRegistry
from src.providers.chat import LLMResponse, ProviderStreamError, ToolCallRequest
from src.swarm.models import (
    RunStatus,
    SwarmAgentSpec,
    SwarmEvent,
    SwarmRun,
    SwarmTask,
    WorkerResult,
)
from src.swarm.store import SwarmStore
from src.swarm.worker import (
    _classify_deliverable,
    _collect_artifacts,
    _filter_skill_descriptions,
    _fx_validation_issue,
    _is_data_agent,
    _is_error_result,
    _report_written,
    _validated_output_from_call,
    run_worker,
)
import src.swarm.runtime as rt
import src.swarm.worker as worker_mod

PLAN_STUB = (
    "### Phase 1 — Plan\n"
    "1. Load the asset-allocation skill\n"
    "2. Fetch data\n\n"
    "### Phase 2 — Execute\n"
    "First, I'll load the necessary skills."
)
REAL_REPORT = (
    "# BTC-USDT — Short-Term View\n\n"
    "Spot fetched via okx: 81,704.6 (2026-05-05). 7d range 77,750–82,842.\n\n"
    "**Recommendation: accumulate on dips to 79k; invalidation below 77.5k.**\n"
    "Position 3% NAV, stop 76,900, target 86,000. Funding 0.035%/8h is elevated\n"
    "but not extreme; on-chain exchange reserves declining (bullish)."
)


# ---- content contract (Hybrid policy) -------------------------------------
def test_plan_only_is_rejected():
    assert _classify_deliverable(
        PLAN_STUB, is_data_agent=True, report_written=False, data_tool_calls=0
    )


def test_unparsed_tool_markup_is_rejected():
    txt = "<｜tool▁calls▁begin｜>function<tool_sep>load_skill"
    assert _classify_deliverable(
        txt, is_data_agent=False, report_written=False, data_tool_calls=0
    )


def test_mock_data_is_rejected():
    txt = "### Risk Audit (Mock Data)\nWorst Drawdown: -23.5% | 95% VaR: -4.2%"
    assert _classify_deliverable(
        txt, is_data_agent=True, report_written=True, data_tool_calls=3
    )


def test_raw_tool_envelope_is_rejected():
    txt = '{"status": "ok", "content": "<skill name=technical-basic>...</skill>"}'
    assert _classify_deliverable(
        txt, is_data_agent=False, report_written=False, data_tool_calls=1
    )


def test_data_agent_without_evidence_is_rejected():
    assert _classify_deliverable(
        REAL_REPORT, is_data_agent=True, report_written=False, data_tool_calls=0
    )


def test_synthesis_agent_prose_is_accepted():
    """FALSE-REJECT GUARD: a tool-less synthesis/editor agent that produced
    real prose with no tool calls and no report.md must pass."""
    assert (
        _classify_deliverable(
            REAL_REPORT, is_data_agent=False, report_written=False, data_tool_calls=0
        )
        is None
    )


def test_real_report_is_accepted():
    assert (
        _classify_deliverable(
            REAL_REPORT, is_data_agent=True, report_written=True, data_tool_calls=5
        )
        is None
    )


def test_empty_skill_assignment_does_not_expand_to_all_skills():
    class _Loader:
        def get_descriptions(self):
            return "ALL-SKILLS-SENTINEL"

        skills = []

    assert _filter_skill_descriptions(_Loader(), []) == "(no matching skills)"


def test_fx_validation_gate_rejects_missing_or_mismatched_machine_output(tmp_path):
    artifact_dir = tmp_path / "artifacts" / "debate_judge"
    artifact_dir.mkdir(parents=True)
    expected = {"evidence_context_id": "fxctx-1", "decision": "wait"}

    assert _fx_validation_issue(artifact_dir, None) == (
        "validate_fx_output never returned valid=true"
    )

    (artifact_dir / "report.md").write_text(
        '```json\n{"evidence_context_id":"fxctx-1","decision":"long"}\n```',
        encoding="utf-8",
    )
    assert _fx_validation_issue(artifact_dir, expected) == (
        "report.md machine JSON differs from the validated output"
    )


def test_fx_validation_gate_accepts_matching_validated_report(tmp_path):
    artifact_dir = tmp_path / "artifacts" / "debate_judge"
    artifact_dir.mkdir(parents=True)
    expected = {"evidence_context_id": "fxctx-1", "decision": "wait"}
    (artifact_dir / "report.md").write_text(
        "# Decision\n\n```json\n" + __import__("json").dumps(expected) + "\n```",
        encoding="utf-8",
    )

    assert _fx_validation_issue(artifact_dir, expected) is None


def test_is_data_agent_classification():
    synth = SwarmAgentSpec(
        id="editor",
        role="Editor",
        system_prompt="x",
        tools=["bash", "read_file", "write_file"],
    )
    analyst = SwarmAgentSpec(
        id="onchain",
        role="On-Chain",
        system_prompt="x",
        tools=["bash", "write_file", "get_market_data"],
    )
    assert _is_data_agent(synth) is False
    assert _is_data_agent(analyst) is True


def test_report_written_detection(tmp_path: Path):
    assert _report_written(tmp_path) is False
    (tmp_path / "report.md").write_text("   \n ", encoding="utf-8")
    assert _report_written(tmp_path) is False
    (tmp_path / "report.md").write_text("# Real report\nbuy.", encoding="utf-8")
    assert _report_written(tmp_path) is True


# ---- runtime integrity ----------------------------------------------------
def _run(tmp_path: Path, worker_result: WorkerResult) -> SwarmRun:
    store = SwarmStore(base_dir=tmp_path)
    runtime = rt.SwarmRuntime(store=store)
    agent = SwarmAgentSpec(
        id="analyst", role="Analyst", system_prompt="x", max_retries=0
    )
    task = SwarmTask(id="t1", agent_id="analyst", prompt_template="do x")
    run = SwarmRun(
        id="r",
        preset_name="demo",
        created_at="2026-01-01T00:00:00Z",
        agents=[agent],
        tasks=[task],
    )
    store.create_run(run)
    runtime._execute_run(run, threading.Event())
    reloaded = store.load_run(run.id)
    assert reloaded is not None
    return reloaded


def test_timeout_terminal_run_not_completed(tmp_path, monkeypatch):
    """fail-before / pass-after anchor: timeout terminal must not be a success."""
    monkeypatch.setattr(
        rt,
        "run_worker",
        lambda *a, **k: WorkerResult(status="timeout", summary="partial work"),
    )
    run = _run(tmp_path, None)
    assert run.status != RunStatus.completed
    assert run.final_report is None


def test_incomplete_terminal_run_not_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rt,
        "run_worker",
        lambda *a, **k: WorkerResult(
            status="incomplete",
            summary=PLAN_STUB,
            error="output contract not met: plan-only stub",
        ),
    )
    run = _run(tmp_path, None)
    assert run.status != RunStatus.completed
    assert run.final_report is None
    assert run.tasks[0].error and "plan-only" in run.tasks[0].error


def test_genuine_completion_still_succeeds(tmp_path, monkeypatch):
    """Guard: a real deliverable must still complete and become final_report."""
    monkeypatch.setattr(
        rt,
        "run_worker",
        lambda *a, **k: WorkerResult(
            status="completed", summary=REAL_REPORT, iterations=4
        ),
    )
    run = _run(tmp_path, None)
    assert run.status == RunStatus.completed
    assert run.final_report == REAL_REPORT


# ---- _is_error_result: JSON parse + truncation fallback -------------------
# Follow-up from #119: the substring head-match could (a) false-positive on
# a nested ``status`` field and (b) false-negate when the envelope sat past
# the 160-char head. Parsing the envelope as JSON pins both.


def test_is_error_result_top_level_error():
    assert _is_error_result('{"status": "error", "error": "bad key"}') is True
    assert _is_error_result('{"status":"error"}') is True


def test_is_error_result_top_level_ok():
    assert _is_error_result('{"status": "ok", "content": "..."}') is False


def test_is_error_result_nested_error_no_false_positive():
    """A nested ``status`` (e.g. inside ``data``) must NOT count — only the
    envelope status matters for the deliverable contract."""
    nested = '{"status": "ok", "data": {"status": "error", "detail": "x"}}'
    assert _is_error_result(nested) is False


def test_is_error_result_error_past_substring_head():
    """G2: an error envelope where ``status`` sits past the 160-char head
    (long preamble in another field). Substring head-match used to miss
    this; JSON parse catches it."""
    long_field = "x" * 200
    payload = '{"meta": "' + long_field + '", "status": "error"}'
    assert _is_error_result(payload) is True


def test_is_error_result_truncated_falls_back_to_substring():
    """Truncated / unparseable JSON still gets the original substring
    classifier; the function must never raise on the worker hot path."""
    truncated = '{"status": "error", "trace": "...'  # missing closing quote
    assert _is_error_result(truncated) is True


def test_is_error_result_non_json_safe():
    assert _is_error_result("") is False
    assert _is_error_result(None) is False  # type: ignore[arg-type]
    assert _is_error_result("plain text output") is False
    assert _is_error_result("[1, 2, 3]") is False  # JSON array, not envelope


def test_is_error_result_other_status_values():
    """Only ``"error"`` counts; ``"warning"`` / ``"degenerate"`` etc. are
    not error envelopes (the worker still credits them as a tool call)."""
    assert _is_error_result('{"status": "degenerate", "warning": "T=0"}') is False
    assert _is_error_result('{"status": "warning"}') is False


class _ResultTool(BaseTool):
    """Return one canned result or raise to exercise ToolRegistry normalization."""

    name = "market_probe"
    description = "Return a canned market result."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, result: str, *, raises: bool = False) -> None:
        self._result = result
        self._raises = raises

    def execute(self, **kwargs) -> str:
        if self._raises:
            raise RuntimeError("local probe failed")
        return self._result


class _ScriptedLLM:
    def __init__(self) -> None:
        self._responses = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="call-1", name="market_probe", arguments={})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="Completed the requested market analysis with a clear conclusion.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]

    def stream_chat(self, messages, tools=None, timeout=None, on_text_chunk=None):
        return self._responses.pop(0)


class _ValidateFxTool(BaseTool):
    name = "validate_fx_output"
    description = "Validate canned FX output."
    parameters = {"type": "object", "properties": {"output": {"type": "object"}}}

    def execute(self, **kwargs) -> str:
        return '{"valid":true,"mode":"decision","errors":[],"warnings":[],"checked_evidence_ids":[]}'


class _WriteReportTool(BaseTool):
    name = "write_file"
    description = "Write a canned report."
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        path = Path(kwargs["run_dir"]) / kwargs["path"]
        path.write_text(kwargs["content"], encoding="utf-8")
        return '{"status":"ok"}'


class _FxContractLLM:
    def __init__(self, *, validate: bool, extra_report_field: bool = False) -> None:
        output = {"evidence_context_id": "fxctx-1", "decision": "wait"}
        report_output = dict(output)
        if extra_report_field:
            report_output["upstream_arguments"] = []
        calls = []
        if validate:
            calls.append(
                ToolCallRequest(
                    id="validate-1",
                    name="validate_fx_output",
                    arguments={"output": output},
                )
            )
        calls.append(
            ToolCallRequest(
                id="write-1",
                name="write_file",
                arguments={
                    "path": "report.md",
                    "content": "```json\n"
                    + __import__("json").dumps(report_output)
                    + "\n```",
                },
            )
        )
        self._responses = [
            LLMResponse(content=None, tool_calls=calls, finish_reason="tool_calls"),
            LLMResponse(
                content="FX review complete with a deterministic contract.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]

    def stream_chat(self, messages, tools=None, timeout=None, on_text_chunk=None):
        return self._responses.pop(0)


class _FxValidateThenWriteLLM:
    """Model that writes the report one turn after validation.

    This mirrors the production prompt: validation and report writing are
    commonly separate Responses turns, so canonicalization must not depend on
    both tool calls appearing in one response.
    """

    def __init__(self, report_content: str) -> None:
        output = {"evidence_context_id": "fxctx-1", "decision": "wait"}
        self._responses = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="validate-1",
                        name="validate_fx_output",
                        arguments={"output": output},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="write-1",
                        name="write_file",
                        arguments={"path": "report.md", "content": report_content},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="FX review complete with a deterministic contract.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]

    def stream_chat(self, messages, tools=None, timeout=None, on_text_chunk=None):
        return self._responses.pop(0)


class _FxDisconnectAfterReportLLM(_FxContractLLM):
    """Persist a valid report, then simulate a transient provider disconnect."""

    def __init__(self, *, validate: bool) -> None:
        super().__init__(validate=validate)
        self._responses = self._responses[:1]

    def stream_chat(self, messages, tools=None, timeout=None, on_text_chunk=None):
        if self._responses:
            return self._responses.pop(0)
        raise ProviderStreamError(
            provider="openai",
            model="test-model",
            original=ConnectionError("simulated EOF after report write"),
        )


@pytest.mark.parametrize(
    ("result", "raises", "expected_event_status", "expected_worker_status"),
    [
        ('{"status": "ok", "data": [1]}', False, "ok", "completed"),
        ('{"status": "error", "trace": "...', False, "error", "incomplete"),
        ("", True, "error", "incomplete"),
    ],
)
def test_tool_result_event_status_and_evidence_credit_agree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: str,
    raises: bool,
    expected_event_status: str,
    expected_worker_status: str,
) -> None:
    registry = ToolRegistry()
    registry.register(_ResultTool(result, raises=raises))
    monkeypatch.setattr(
        worker_mod, "build_swarm_registry", lambda *args, **kwargs: registry
    )
    monkeypatch.setattr(worker_mod, "ChatLLM", lambda *args, **kwargs: _ScriptedLLM())

    events: list[SwarmEvent] = []
    worker_result = run_worker(
        agent_spec=SwarmAgentSpec(
            id="analyst",
            role="Analyst",
            system_prompt="Analyse the result.",
            tools=["market_probe"],
            max_iterations=3,
        ),
        task=SwarmTask(
            id="task", agent_id="analyst", prompt_template="Probe the market."
        ),
        upstream_summaries={},
        user_vars={},
        run_dir=tmp_path,
        event_callback=events.append,
    )

    tool_result = next(event for event in events if event.type == "tool_result")
    assert tool_result.data["status"] == expected_event_status
    assert worker_result.status == expected_worker_status


@pytest.mark.parametrize(
    ("validate", "expected_status"),
    [(False, "failed"), (True, "completed")],
)
def test_fx_worker_completion_requires_matching_validated_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate: bool,
    expected_status: str,
) -> None:
    registry = ToolRegistry()
    registry.register(_ValidateFxTool())
    registry.register(_WriteReportTool())
    monkeypatch.setattr(
        worker_mod, "build_swarm_registry", lambda *args, **kwargs: registry
    )
    monkeypatch.setattr(
        worker_mod, "ChatLLM", lambda *args, **kwargs: _FxContractLLM(validate=validate)
    )

    result = run_worker(
        agent_spec=SwarmAgentSpec(
            id="debate_judge",
            role="FX Judge",
            system_prompt="Validate and write the decision.",
            tools=["validate_fx_output", "write_file"],
            max_iterations=3,
        ),
        task=SwarmTask(
            id="task-judge",
            agent_id="debate_judge",
            prompt_template="Decide.",
        ),
        upstream_summaries={},
        user_vars={},
        run_dir=tmp_path,
    )

    assert result.status == expected_status
    if not validate:
        assert "never returned valid=true" in str(result.error)


def test_fx_worker_canonicalizes_report_to_last_validated_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-validation report rewrite cannot invalidate a valid FX run."""
    registry = ToolRegistry()
    registry.register(_ValidateFxTool())
    registry.register(_WriteReportTool())
    monkeypatch.setattr(
        worker_mod, "build_swarm_registry", lambda *args, **kwargs: registry
    )
    monkeypatch.setattr(
        worker_mod,
        "ChatLLM",
        lambda *args, **kwargs: _FxContractLLM(validate=True, extra_report_field=True),
    )

    result = run_worker(
        agent_spec=SwarmAgentSpec(
            id="debate_judge",
            role="FX Judge",
            system_prompt="Validate and write the decision.",
            tools=["validate_fx_output", "write_file"],
            max_iterations=3,
        ),
        task=SwarmTask(
            id="task-judge",
            agent_id="debate_judge",
            prompt_template="Decide.",
        ),
        upstream_summaries={},
        user_vars={},
        run_dir=tmp_path,
    )

    assert result.status == "completed"
    report = (tmp_path / "artifacts" / "debate_judge" / "report.md").read_text(
        encoding="utf-8"
    )
    assert "upstream_arguments" not in report
    assert (
        worker_mod._fx_validation_issue(
            tmp_path / "artifacts" / "debate_judge",
            {"evidence_context_id": "fxctx-1", "decision": "wait"},
        )
        is None
    )


@pytest.mark.parametrize(
    "report_content",
    [
        (
            "# Decision\n\n## Machine-readable V2\n\n```json\n"
            '{"evidence_context_id":"fxctx-1","decision":"wait",'
            '"stale_copy":true}\n```'
        ),
        "# Decision\n\n## Machine-readable V2\n\n```json\n{truncated\n```",
        "# Decision\n\nThe validated decision is wait, with no directional exposure.",
    ],
)
def test_fx_worker_canonicalizes_report_written_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_content: str,
) -> None:
    """A later report turn cannot reintroduce stale or missing machine JSON."""
    registry = ToolRegistry()
    registry.register(_ValidateFxTool())
    registry.register(_WriteReportTool())
    monkeypatch.setattr(
        worker_mod, "build_swarm_registry", lambda *args, **kwargs: registry
    )
    monkeypatch.setattr(
        worker_mod,
        "ChatLLM",
        lambda *args, **kwargs: _FxValidateThenWriteLLM(report_content),
    )

    result = run_worker(
        agent_spec=SwarmAgentSpec(
            id="debate_judge",
            role="FX Judge",
            system_prompt="Validate and write the decision.",
            tools=["validate_fx_output", "write_file"],
            max_iterations=5,
        ),
        task=SwarmTask(
            id="task-judge",
            agent_id="debate_judge",
            prompt_template="Decide.",
        ),
        upstream_summaries={},
        user_vars={},
        run_dir=tmp_path,
    )

    assert result.status == "completed"
    artifact_dir = tmp_path / "artifacts" / "debate_judge"
    report = (artifact_dir / "report.md").read_text(encoding="utf-8")
    match = worker_mod._machine_json_match(report)
    assert match is not None
    assert __import__("json").loads(match.group(1)) == {
        "evidence_context_id": "fxctx-1",
        "decision": "wait",
    }
    assert (
        worker_mod._fx_validation_issue(
            artifact_dir,
            {"evidence_context_id": "fxctx-1", "decision": "wait"},
        )
        is None
    )


def test_fx_worker_keeps_valid_report_when_provider_disconnects_after_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ToolRegistry()
    registry.register(_ValidateFxTool())
    registry.register(_WriteReportTool())
    monkeypatch.setattr(
        worker_mod, "build_swarm_registry", lambda *args, **kwargs: registry
    )
    monkeypatch.setattr(
        worker_mod,
        "ChatLLM",
        lambda *args, **kwargs: _FxDisconnectAfterReportLLM(validate=True),
    )

    result = run_worker(
        agent_spec=SwarmAgentSpec(
            id="debate_judge",
            role="FX Judge",
            system_prompt="Validate and write the decision.",
            tools=["validate_fx_output", "write_file"],
            max_iterations=3,
        ),
        task=SwarmTask(
            id="task-judge",
            agent_id="debate_judge",
            prompt_template="Decide.",
        ),
        upstream_summaries={},
        user_vars={},
        run_dir=tmp_path,
    )

    assert result.status == "completed"
    assert result.error and "simulated EOF" in result.error


def test_validated_fx_output_is_normalized_before_downstream_handoff() -> None:
    """Compact aliases are expanded, including deterministic claim IDs."""
    output = {
        "schema_version": "2.0",
        "agent_role": "pair_bull",
        "hypothesis_status": "weak",
        "summary": "A weak, falsifiable upside hypothesis.",
        "causal_chains": [
            {
                "chain_id": "chain-1",
                "driver": "EU growth surprise [fxe-growth]",
                "inference": "The relative growth impulse may support EUR.",
                "transmission": "Expect repricing of the EUR leg.",
                "expected_effect": "up",
                "window": "2 weeks",
                "evidence_ids": ["fxe-growth"],
            }
        ],
        "catalysts": [],
        "market_confirmations": [],
        "strongest_countercase": [
            {
                "statement": "4H confirmation is still missing.",
                "evidence_ids": ["fxe-growth"],
            }
        ],
        "invalidation_conditions": ["The observed growth impulse reverses."],
        "coverage": {
            "domains": ["macro"],
            "evidence_family_ids": ["macro:growth"],
            "limitations": ["No 4H confirmation."],
        },
        "strength": "weak",
        "missing_data": ["4H bars"],
        "tool_calls": [],
        "evidence_context_id": "fxctx-1",
    }
    normalized = _validated_output_from_call(
        {"output": output},
        '{"valid":true,"mode":"hypothesis","errors":[],"warnings":[],"checked_evidence_ids":[]}',
    )

    assert normalized is not None
    assert normalized["causal_chains"][0]["claim_id"] == "chain-1"
    assert normalized["causal_chains"][0]["transmission_mechanism"] == (
        "Expect repricing of the EUR leg."
    )
    assert "chain_id" not in normalized["causal_chains"][0]


def test_fx_validation_failure_exposes_actionable_errors(tmp_path: Path) -> None:
    issue = worker_mod._fx_validation_issue(
        tmp_path,
        None,
        [
            {
                "code": "SCHEMA_VALIDATION_ERROR",
                "path": "$.findings[0].statement",
                "message": "Field required",
            }
        ],
    )

    assert issue is not None
    assert "never returned valid=true" in issue
    assert "findings[0].statement" in issue
    assert "Field required" in issue


def test_worker_events_expose_detailed_agent_and_tool_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ToolRegistry()
    registry.register(_ResultTool('{"status":"ok","data":{"price":1.1034}}'))
    monkeypatch.setattr(
        worker_mod, "build_swarm_registry", lambda *args, **kwargs: registry
    )
    monkeypatch.setattr(worker_mod, "ChatLLM", lambda *args, **kwargs: _ScriptedLLM())
    events: list[SwarmEvent] = []

    run_worker(
        agent_spec=SwarmAgentSpec(
            id="analyst",
            role="FX Analyst",
            system_prompt="Use internal evidence only.",
            tools=["market_probe"],
            max_iterations=3,
        ),
        task=SwarmTask(
            id="task",
            agent_id="analyst",
            prompt_template="Analyse {symbol}.",
        ),
        upstream_summaries={},
        user_vars={"symbol": "EURUSD"},
        run_dir=tmp_path,
        event_callback=events.append,
    )

    started = next(event for event in events if event.type == "worker_started")
    tool_call = next(event for event in events if event.type == "tool_call")
    tool_result = next(event for event in events if event.type == "tool_result")
    completed = next(event for event in events if event.type == "worker_completed")

    assert started.data["input"]["user_prompt"] == "Analyse EURUSD."
    assert "Use internal evidence only." in started.data["input"]["system_prompt"]
    assert started.data["tools"] == ["market_probe"]
    assert tool_call.data["call_id"] == "call-1"
    assert tool_call.data["input"] == {}
    assert tool_result.data["call_id"] == "call-1"
    assert tool_result.data["output"]["data"]["price"] == 1.1034
    assert "clear conclusion" in completed.data["output"]


def test_collect_artifacts_recurses_and_returns_sorted_run_relative_paths(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "run" / "artifacts" / "analyst"
    (artifact_dir / "nested").mkdir(parents=True)
    (artifact_dir / "other").mkdir()
    (artifact_dir / "summary.md").write_text("summary", encoding="utf-8")
    (artifact_dir / "nested" / "report.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "other" / "report.json").write_text("{}", encoding="utf-8")

    assert _collect_artifacts(artifact_dir) == [
        "artifacts/analyst/nested/report.json",
        "artifacts/analyst/other/report.json",
        "artifacts/analyst/summary.md",
    ]


def test_collect_artifacts_rejects_symlink_escape(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "run" / "artifacts" / "analyst"
    artifact_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    escape = artifact_dir / "escape.txt"
    try:
        escape.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    assert _collect_artifacts(artifact_dir) == []
