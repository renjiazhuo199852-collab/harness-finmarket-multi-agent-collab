"""专用 run_fx_debate 入口的端到端编排边界测试。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.fx_debate.evidence_sources import RawFxSnapshot
from src.swarm.models import SwarmRun
from src.swarm.store import SwarmStore
from src.tools.run_fx_debate_tool import (
    DefaultFxDebateOrchestrator,
    FxDebateExecution,
    RunFxDebateTool,
    _extract_json,
    _normalize_run_options,
    _persist_data_trace_events,
)
from src.fx_debate.models import RunOptions


def _resolved_request() -> dict:
    return {
        "status": "resolved",
        "asset_class": "fx",
        "instrument_type": "spot",
        "pair_class": "major",
        "canonical_symbol": "EURUSD",
        "display_symbol": "EUR/USD",
        "base_currency": "EUR",
        "quote_currency": "USD",
        "requested_base_currency": "EUR",
        "requested_quote_currency": "USD",
        "inverted": False,
        "horizon": "2 weeks",
        "timeframe": "4H/1D",
    }


def test_date_only_as_of_is_normalized_to_aware_utc_datetime() -> None:
    options = RunOptions.model_validate(
        _normalize_run_options({"as_of": "2026-08-20"})
    )

    assert options.as_of is not None
    assert options.as_of.isoformat() == "2026-08-20T00:00:00+00:00"


def test_current_quote_timeframe_returns_user_facing_guidance() -> None:
    payload = json.loads(
        RunFxDebateTool(evidence_source=_FakeEvidenceSource()).execute(
            target="EURUSD",
            timeframe="today",
            goal="查询今天 EURUSD 汇率",
        )
    )

    assert payload["status"] == "error"
    assert payload["error"]["code"] == "FX_DEBATE_TIMEFRAME_REQUIRED"
    assert "当前汇率" in payload["error"]["message"]


@dataclass
class _FakeOrchestrator:
    root: Path
    called: bool = False
    variables: dict[str, str] | None = None

    def run(self, *, preset_name, user_vars, context, bundle):
        self.called = True
        self.variables = user_vars
        run_root = self.root / "swarm-test"
        run_root.mkdir()
        evidence_id = bundle.evidence[0].evidence_id

        def argument(role: str, prefix: str, direction: str) -> dict:
            return {
                "schema_version": "2.0",
                "evidence_context_id": context.evidence_context_id,
                "agent_role": role,
                "hypothesis_direction": direction,
                "hypothesis_status": "weak",
                "summary": "测试论证。",
                "causal_chains": [
                    {
                        "claim_id": f"{prefix}_c1",
                        "observed_fact": "报价存在。",
                        "inference": "价格证据仍需确认。",
                        "transmission_mechanism": "当前证据只支持观望。",
                        "expected_effect": direction,
                        "effective_window": "未来两周",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "catalysts": [],
                "market_confirmations": [],
                "strongest_countercase": [
                    {"statement": "数据不足。", "evidence_ids": [evidence_id]}
                ],
                "invalidation_conditions": [],
                "coverage": {
                    "domains": ["market"],
                    "evidence_family_ids": [bundle.evidence[0].evidence_family_id],
                    "limitations": ["测试环境仅有报价"],
                },
                "strength": "low",
                "missing_data": ["测试环境仅有一项证据"],
                "tool_calls": [],
            }

        bull = argument("pair_bull", "bull", "up")
        bear = argument("pair_bear", "bear", "down")
        macro = {
            "schema_version": "2.0",
            "evidence_context_id": context.evidence_context_id,
            "agent_role": "relative_macro_technical",
            "analysis_status": "partial",
            "relative_macro_state": "indeterminate",
            "technical_state": "indeterminate",
            "cross_confirmation": "indeterminate",
            "findings": [
                {
                    "claim_id": "mt_c1",
                    "dimension": "technical",
                    "statement": "技术方向待确认。",
                    "evidence_ids": [evidence_id],
                }
            ],
            "event_state": "unknown",
            "reliability": "low",
            "summary": "数据不足。",
            "missing_data": ["测试环境仅有一项证据"],
            "tool_calls": [],
        }
        risk = {
            "evidence_context_id": context.evidence_context_id,
            "approved_claim_ids": ["bull_c1", "bear_c1", "mt_c1"],
            "rejected_claims": [],
            "duplicate_claim_groups": [],
            "evidence_conflicts": [],
            "risk_level": "medium",
            "allowed_actions": ["wait", "hedge"],
            "risk_limit": {
                "max_risk_per_trade_pct": 0.5,
                "basis": "证据不完整。",
            },
            "required_invalidation_conditions": ["价格突破后重评"],
            "missing_data": ["测试环境仅有一项证据"],
            "risk_summary": "建议等待。",
        }
        decision = {
            "evidence_context_id": context.evidence_context_id,
            "canonical_symbol": context.canonical_symbol,
            "display_symbol": context.display_symbol,
            "requested_symbol": context.display_symbol,
            "inverted": False,
            "direction_semantics": f"long {context.canonical_symbol} = 买入基准货币、卖出报价货币。",
            "decision": "wait",
            "confidence": 0.5,
            "horizon_days": 14,
            "scenario_probabilities": {"bull": 0.3, "base": 0.4, "bear": 0.3},
            "thesis": "当前证据不足以支持立即建立方向仓位。",
            "adopted_claim_ids": ["bull_c1", "bear_c1", "mt_c1"],
            "rejected_claim_ids": [],
            "key_evidence_ids": [evidence_id],
            "trade_plan": {
                "entry_zone": None,
                "stop_loss": None,
                "targets": [],
            },
            "risk_assessment": "证据不完整且方向冲突。",
            "invalidation_conditions": ["价格突破后重评"],
            "missing_data": ["测试环境仅有一项证据"],
            "data_as_of": context.as_of.isoformat(),
            "next_review_trigger": "价格突破后重评。",
        }

        def report(payload: dict) -> str:
            return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

        outputs = {
            "task-pair-bull": report(bull),
            "task-pair-bear": report(bear),
            "task-macro-technical": report(macro),
            "task-risk": report(risk),
            "task-judge": report(decision),
        }
        return FxDebateExecution(
            run_id="swarm-test",
            status="completed",
            final_report=outputs["task-judge"],
            task_outputs=outputs,
            run_root=run_root,
        )


class _FakeEvidenceSource:
    def load(self, context):
        return RawFxSnapshot(
            source_name="excel",
            prices=[
                {
                    "price_time": context.as_of,
                    "last": 1.085,
                    "bid": 1.0849,
                    "ask": 1.0851,
                    "mid": 1.085,
                    "source": "TEST",
                    "source_identifier": f"{context.base_currency}=",
                }
            ],
        )


def test_default_orchestrator_seeds_run_id_before_forwarding_live_events(
    monkeypatch,
    tmp_path,
) -> None:
    """FX Session SSE must receive one canonical run seed before task events."""
    pending_run = SimpleNamespace(
        id="swarm-live-test",
        status=SimpleNamespace(value="pending"),
        agents=[SimpleNamespace(model_dump=lambda mode=None: {"id": "pair_bull", "role": "Pair Bull"})],
        tasks=[SimpleNamespace(model_dump=lambda mode=None: {"id": "task-bull", "agent_id": "pair_bull", "status": "pending"})],
    )
    completed_run = SimpleNamespace(
        id=pending_run.id,
        status=SimpleNamespace(value="completed"),
        final_report="",
        tasks=[SimpleNamespace(id="task-bull", summary="done")],
    )

    class FakeStore:
        def __init__(self, base_dir):
            self.base_dir = base_dir

        def load_run(self, run_id):
            return completed_run if run_id == pending_run.id else None

        def run_dir(self, run_id):
            return tmp_path / run_id

    class FakeRuntime:
        def __init__(self, store, max_workers=4, agent_config=None):
            self.store = store

        def start_run(
            self,
            preset_name,
            user_vars,
            live_callback=None,
            include_shell_tools=False,
            trusted_context=None,
        ):
            assert live_callback is not None
            live_callback(
                {
                    "type": "task_started",
                    "agent_id": "pair_bull",
                    "task_id": "task-bull",
                    "data": {},
                    "timestamp": "2026-08-18T08:00:00+00:00",
                }
            )
            return pending_run

        def cancel_run(self, run_id):
            return True

    # Import and replace runtime modules before narrowing get_env_config; the
    # real module has import-time worker settings that need the full config.
    monkeypatch.setattr("src.swarm.runtime.SwarmRuntime", FakeRuntime)
    monkeypatch.setattr("src.swarm.store.SwarmStore", FakeStore)
    monkeypatch.setattr("src.swarm.store.swarm_runs_root", lambda: tmp_path)
    monkeypatch.setattr("src.config.load_swarm_agent_config", lambda: None)
    monkeypatch.setattr(
        "src.config.accessor.get_env_config",
        lambda: SimpleNamespace(
            swarm=SimpleNamespace(swarm_max_workers=4, swarm_timeout=5)
        ),
    )
    monkeypatch.setattr(
        "src.tools.run_fx_debate_tool.context_request_json",
        lambda user_vars, context: "{}",
    )

    events: list[dict] = []
    execution = DefaultFxDebateOrchestrator().run(
        preset_name="fx_debate_team",
        user_vars={"target": "EURUSD", "timeframe": "2 weeks; 4H/1D", "goal": "test"},
        context=SimpleNamespace(
            evidence_context_id="ctx-live-test",
            model_dump_json=lambda: "{}",
        ),
        bundle=SimpleNamespace(model_dump_json=lambda: "{}"),
        event_callback=events.append,
    )

    assert execution.run_id == "swarm-live-test"
    assert events[0]["type"] == "swarm_started"
    assert events[0]["run_id"] == "swarm-live-test"
    assert events[0]["agents"][0]["id"] == "pair_bull"
    assert events[1]["type"] == "task_started"
    assert events[1]["run_id"] == "swarm-live-test"


def test_machine_contract_can_be_extracted_from_detailed_markdown() -> None:
    report = """# EUR/USD 上涨假设审阅

## 执行摘要

当前证据为 partial，结论保持 weak。[quote-1]

## Machine-readable V2

```json
{"schema_version":"2.0","summary":"保持弱假设"}
```
"""

    assert _extract_json(report) == {
        "schema_version": "2.0",
        "summary": "保持弱假设",
    }


def test_run_fx_debate_builds_context_validates_dag_outputs_and_renders_report(
    tmp_path,
) -> None:
    orchestrator = _FakeOrchestrator(tmp_path)
    tool = RunFxDebateTool(
        orchestrator=orchestrator,
        evidence_source=_FakeEvidenceSource(),
    )

    output = json.loads(
        tool.execute(
            target="EUR/USD",
            timeframe="2 weeks; 4H/1D",
            goal="分析 EURUSD 未来两周走势。",
            run_options={
                "request_id": "req-entry",
                "as_of": "2025-07-23T12:00:00+00:00",
                "risk_profile": "balanced",
                "language": "zh-CN",
            },
        )
    )

    assert output["ok"] is True
    assert output["status"] == "completed"
    assert output["run_id"] == "swarm-test"
    assert output["request_id"] == "req-entry"
    assert output["preset"] == "fx_debate_team"
    assert output["data_source_policy"] == "excel"
    assert (
        output["data_preview"]["evidence_context_id"] == output["evidence_context_id"]
    )
    assert output["data_preview"]["counts"]["market"] == 1
    assert (
        output["data_preview"]["domains"]["market"]["rows"][0]["value"]["mid"] == 1.085
    )
    assert output["decision"]["decision"] == "wait"
    assert output["decision"]["canonical_symbol"] == "EURUSD"
    assert "# EUR/USD 外汇 Debate 结论" in output["report_markdown"]
    assert "当前建议：观望" in output["report_markdown"]
    assert "本地只读 Excel 冻结证据包" in output["report_markdown"]
    assert orchestrator.called is True
    assert orchestrator.variables is not None
    assert orchestrator.variables == {
        "target": "EUR/USD",
        "timeframe": "2 weeks; 4H/1D",
        "goal": "分析 EURUSD 未来两周走势。",
    }
    assert output["upstream_decision"] == {
        "decision": "wait",
        "risk_action": "none",
    }


def test_run_fx_debate_normalizes_common_planner_option_aliases(tmp_path) -> None:
    """Model-emitted medium/zh aliases must not fail before routing starts."""
    output = json.loads(
        RunFxDebateTool(
            orchestrator=_FakeOrchestrator(tmp_path),
            evidence_source=_FakeEvidenceSource(),
        ).execute(
            target="EURUSD",
            timeframe="2 weeks; 4H/1D",
            goal="分析 EURUSD 未来两周走势。",
            run_options={
                "risk_profile": "medium",
                "language": "zh",
                "as_of": "2025-07-23",
            },
        )
    )

    assert output["ok"] is True


def test_run_fx_debate_rejects_ambiguous_public_and_legacy_inputs(tmp_path) -> None:
    output = json.loads(
        RunFxDebateTool(
            orchestrator=_FakeOrchestrator(tmp_path),
            evidence_source=_FakeEvidenceSource(),
        ).execute(
            target="EURUSD",
            timeframe="2 weeks; 4H/1D",
            goal="测试。",
            resolved_request=_resolved_request(),
        )
    )

    assert output["ok"] is False
    assert output["error"]["code"] == "AMBIGUOUS_INPUT"


def test_run_fx_debate_rejects_unresolved_or_inconsistent_input(tmp_path) -> None:
    orchestrator = _FakeOrchestrator(tmp_path)
    request = _resolved_request()
    request["canonical_symbol"] = "USDJPY"

    output = json.loads(
        RunFxDebateTool(
            orchestrator=orchestrator,
            evidence_source=_FakeEvidenceSource(),
        ).execute(
            resolved_request=request,
            run_options={},
        )
    )

    assert output["ok"] is False
    assert output["status"] == "error"
    assert orchestrator.called is False


def test_run_fx_debate_accepts_a_second_major_pair(tmp_path) -> None:
    orchestrator = _FakeOrchestrator(tmp_path)
    request = _resolved_request()
    request.update(
        canonical_symbol="GBPUSD",
        display_symbol="GBP/USD",
        base_currency="GBP",
        requested_base_currency="GBP",
    )

    output = json.loads(
        RunFxDebateTool(
            orchestrator=orchestrator,
            evidence_source=_FakeEvidenceSource(),
        ).execute(
            resolved_request=request,
            run_options={},
        )
    )

    assert output["ok"] is True
    assert output["decision"]["canonical_symbol"] == "GBPUSD"
    assert orchestrator.called is True


def test_run_fx_debate_forwards_live_events_to_the_observer(tmp_path) -> None:
    class _ObservedOrchestrator(_FakeOrchestrator):
        def run(self, *, preset_name, user_vars, context, bundle, event_callback):
            event_callback(
                {
                    "type": "agent_started",
                    "agent_id": "pair_bull",
                    "task_id": "task-pair-bull",
                    "data": {"input": {"user_prompt": "分析 EURUSD"}},
                }
            )
            return super().run(
                preset_name=preset_name,
                user_vars=user_vars,
                context=context,
                bundle=bundle,
            )

    observed: list[dict] = []
    output = json.loads(
        RunFxDebateTool(
            orchestrator=_ObservedOrchestrator(tmp_path),
            event_callback=observed.append,
            evidence_source=_FakeEvidenceSource(),
        ).execute(
            resolved_request=_resolved_request(),
            run_options={"as_of": "2025-07-23T12:00:00+00:00"},
        )
    )

    assert output["ok"] is True
    assert observed[0]["type"] == "context_ready"
    assert observed[0]["data"]["data_preview"]["counts"]["market"] == 1
    assert observed[1] == {
        "type": "agent_started",
        "agent_id": "pair_bull",
        "task_id": "task-pair-bull",
        "data": {"input": {"user_prompt": "分析 EURUSD"}},
    }


def test_run_fx_debate_persists_sdk_and_mcp_trace_events(tmp_path, monkeypatch) -> None:
    runs_root = tmp_path / "runs"
    store = SwarmStore(runs_root)
    store.create_run(
        SwarmRun(
            id="swarm-trace-test",
            preset_name="fx_debate_team",
            user_vars={},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    monkeypatch.setattr("src.swarm.store.swarm_runs_root", lambda: runs_root)

    _persist_data_trace_events(
        "swarm-trace-test",
        [
            {"type": "context_ready", "data": {"source": "excel"}},
            {"type": "data_service.stage", "stage": "dataset_catalog", "progress": 1},
            {"type": "worker_text", "data": {"content": "not a data trace"}},
        ],
    )

    events = store.read_events("swarm-trace-test")
    assert [event.type for event in events] == ["context_ready", "data_service.stage"]
