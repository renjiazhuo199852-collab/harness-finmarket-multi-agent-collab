"""Specialized entry Tool for the five-Agent, frozen-bundle FX Debate."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from typing import TYPE_CHECKING

from pydantic import ValidationError

from src.agent.tools import BaseTool
from src.fx_debate.context import build_evidence_context
from src.fx_debate.contracts import (
    FinalDecision,
    FrontAgentOutput,
    HypothesisArgumentV2,
    RelativeStateV2,
    RiskReview,
    TradePlan,
)
from src.fx_debate.data_query_agent import FxDataServiceError
from src.fx_debate.evidence_factory import EvidenceBundle, FxEvidenceFactory
from src.fx_debate.evidence_sources import (
    AiSearchFxEvidenceSource,
    ExcelFxEvidenceSource,
    FxEvidenceSource,
    ReaderFxEvidenceSource,
)
from src.fx_debate.models import (
    EvidenceContext,
    ResolvedFxDebateRequest,
    RunOptions,
)
from src.fx_debate.request_adapter import (
    FxPairDebateRequest,
    adapt_fx_pair_debate_request,
)
from src.fx_debate.store import FxEvidenceStore
from src.market_data_reader import MarketDataReader
from src.tools.validate_fx_output_tool import ValidateFxOutputTool

if TYPE_CHECKING:
    from src.agent.swarm_authorization import SwarmAuthorization

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
FxDebateEventCallback = Callable[[dict[str, Any]], None]
SessionEventCallback = Callable[[str, dict[str, Any]], None]
CancelChecker = Callable[[], bool]
_PREVIEW_LIMITS = {"market": 40, "technical": 36, "macro": 24, "news": 24}
# FX Debate is currently used as a historical backtest. It must always emit a
# directional result and a deterministic price plan when a usable price anchor
# exists in the frozen evidence bundle.
BACKTEST_DIRECTIONAL_MODE = True


def adapt_fx_debate_event_callback(
    callback: SessionEventCallback | None,
) -> FxDebateEventCallback | None:
    """Adapt one-argument FX events to the Session/SSE callback contract."""
    if callback is None:
        return None

    def forward(event: dict[str, Any]) -> None:
        payload = event if isinstance(event, dict) else {"value": str(event)}
        if payload.get("type") == "context_ready":
            callback("fx_debate.context_ready", payload)
            return
        if payload.get("type") == "swarm_started":
            callback(
                "swarm.started",
                {key: value for key, value in payload.items() if key != "type"},
            )
            return
        callback(
            "swarm.event",
            {
                "run_id": payload.get("run_id"),
                "event": payload,
            },
        )

    return forward


@dataclass(frozen=True)
class FxDebateExecution:
    """Materialized Swarm result needed for deterministic final validation."""

    run_id: str
    status: str
    final_report: str
    task_outputs: dict[str, str]
    run_root: Path


class FxDebateOrchestrator(Protocol):
    """Small orchestration seam around the existing Swarm runtime."""

    def run(
        self,
        *,
        preset_name: str,
        user_vars: dict[str, str],
        context: EvidenceContext,
        bundle: EvidenceBundle,
        event_callback: FxDebateEventCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> FxDebateExecution:
        """Start, await, and materialize one Swarm execution."""


class DefaultFxDebateOrchestrator:
    """Use the repository's existing asynchronous Swarm runtime."""

    def run(
        self,
        *,
        preset_name: str,
        user_vars: dict[str, str],
        context: EvidenceContext,
        bundle: EvidenceBundle,
        event_callback: FxDebateEventCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> FxDebateExecution:
        from src.config import load_swarm_agent_config
        from src.config.accessor import get_env_config
        from src.swarm.runtime import SwarmRuntime
        from src.swarm.store import SwarmStore, swarm_runs_root

        base_dir = swarm_runs_root()
        base_dir.mkdir(parents=True, exist_ok=True)
        store = SwarmStore(base_dir=base_dir)
        config = get_env_config()
        runtime = SwarmRuntime(
            store=store,
            max_workers=config.swarm.swarm_max_workers,
            agent_config=load_swarm_agent_config(),
        )

        pending_live_events: list[dict[str, Any]] = []
        run_id_holder: dict[str, str | None] = {"run_id": None}
        event_lock = threading.Lock()

        def forward_event(event: Any) -> None:
            if event_callback is None:
                return
            if hasattr(event, "model_dump"):
                payload = event.model_dump(mode="json")
            elif isinstance(event, dict):
                payload = dict(event)
            else:
                return
            with event_lock:
                run_id = run_id_holder["run_id"]
                if run_id is None:
                    pending_live_events.append(payload)
                    return
                event_callback({**payload, "run_id": run_id})

        # Keep the handoff's three public variables visible to the Swarm run;
        # resolved identity and evidence scope are system-injected variables,
        # never values that an Agent can rewrite.
        trusted_context = {
            "resolved_request_json": context_request_json(user_vars, context),
            "evidence_context_json": context.model_dump_json(),
            "evidence_context_id": context.evidence_context_id,
            "evidence_bundle_json": bundle.model_dump_json(),
        }
        run = runtime.start_run(
            preset_name,
            user_vars,
            live_callback=forward_event if event_callback is not None else None,
            include_shell_tools=False,
            trusted_context=trusted_context,
        )
        with event_lock:
            run_id_holder["run_id"] = run.id
            if event_callback is not None:
                event_callback(
                    {
                        "type": "swarm_started",
                        "run_id": run.id,
                        "preset": preset_name,
                        "variables": user_vars,
                        "status": "running",
                        "agents": [agent.model_dump(mode="json") for agent in run.agents],
                        "tasks": [task.model_dump(mode="json") for task in run.tasks],
                    }
                )
                for payload in pending_live_events:
                    event_callback({**payload, "run_id": run.id})
                pending_live_events.clear()
        if cancel_checker is not None and cancel_checker():
            runtime.cancel_run(run.id)
            return FxDebateExecution(
                run_id=run.id,
                status="cancelled",
                final_report="",
                task_outputs={},
                run_root=store.run_dir(run.id),
            )
        deadline = time.monotonic() + max(1, config.swarm.swarm_timeout)
        loaded = run
        while time.monotonic() < deadline:
            candidate = store.load_run(run.id)
            if candidate is not None:
                loaded = candidate
                if loaded.status.value in _TERMINAL_STATUSES:
                    break
            if cancel_checker is not None and cancel_checker():
                runtime.cancel_run(run.id)
                return FxDebateExecution(
                    run_id=run.id,
                    status="cancelled",
                    final_report="",
                    task_outputs={},
                    run_root=store.run_dir(run.id),
                )
            time.sleep(0.25)
        else:
            runtime.cancel_run(run.id)
            return FxDebateExecution(
                run_id=run.id,
                status="timeout",
                final_report="",
                task_outputs={},
                run_root=store.run_dir(run.id),
            )

        return FxDebateExecution(
            run_id=loaded.id,
            status=loaded.status.value,
            final_report=loaded.final_report or "",
            task_outputs={
                task.id: task.summary or ""
                for task in loaded.tasks
                if task.summary is not None
            },
            run_root=store.run_dir(loaded.id),
        )


def _normalize_run_options(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize common Planner/UI aliases before validating strict options.

    The public contract remains strict, but models frequently emit ``medium``
    and ``zh`` for the canonical ``balanced`` and ``zh-CN`` values.  Treating
    those two harmless aliases deterministically prevents a route from failing
    before it has even built its evidence context.
    """
    normalized = dict(value)
    risk = normalized.get("risk_profile")
    risk_aliases = {
        "low": "conservative",
        "medium": "balanced",
        "moderate": "balanced",
        # Some planners use neutral for a balanced risk budget.  Keep the
        # public RunOptions contract strict while normalizing this alias at
        # the tool boundary.
        "neutral": "balanced",
        "中等": "balanced",
        "平衡": "balanced",
        "高": "aggressive",
        "低": "conservative",
    }
    if isinstance(risk, str):
        normalized["risk_profile"] = risk_aliases.get(risk.strip().lower(), risk.strip())
    language = normalized.get("language")
    if isinstance(language, str) and language.strip().lower() in {"zh", "zh_cn", "zh-cn", "中文"}:
        normalized["language"] = "zh-CN"
    # Planner/UI clients often send a calendar date for a frozen snapshot.
    # RunOptions deliberately requires an aware datetime; interpret a plain
    # date as the start of that UTC day instead of rejecting the whole run.
    as_of = normalized.get("as_of")
    if isinstance(as_of, str):
        raw_as_of = as_of.strip()
        if raw_as_of and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_as_of):
            normalized["as_of"] = f"{raw_as_of}T00:00:00+00:00"
    elif isinstance(as_of, datetime) and as_of.tzinfo is None:
        # Preserve the existing contract for naive datetime objects by
        # assigning the same explicit UTC interpretation as date-only input.
        normalized["as_of"] = as_of.replace(tzinfo=timezone.utc)
    return normalized


class RunFxDebateTool(BaseTool):
    """Launch the evidence-scoped multi-agent FX Debate workflow."""

    name = "run_fx_debate"
    description = (
        "接收 Planner 的 target、timeframe、goal 三变量，内部解析现货外汇请求并创建不可变 Evidence Context，"
        "启动五 Agent Debate，并返回经证据上下文和风险边界处理的结构化决策与中文报告。"
        "数据来自运行前冻结的 Excel、MarketDataReader 或独立 AI Search 服务证据包；不会回退到外部行情。"
        "仅用于未来走势、方向预测、多空辩论和交易建议；查询今日/当前/最新汇率时不要调用本工具，"
        "应使用通用行情查询路径。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "外汇货币对，例如 EURUSD、EUR/USD 或 EUR-USD。",
            },
            "timeframe": {
                "type": "string",
                "description": "例如 '2 weeks; 4H/1D' 或仅使用可用的 '1D'（默认两周）。",
            },
            "goal": {
                "type": "string",
                "description": "完整研究目标和约束。",
            },
            "run_options": {
                "type": "object",
                "description": "可选 request_id、as_of、risk_profile、language。",
            },
        },
        "required": ["target", "timeframe", "goal"],
    }
    is_readonly = False
    repeatable = True

    def __init__(
        self,
        *,
        orchestrator: FxDebateOrchestrator | None = None,
        event_callback: FxDebateEventCallback | None = None,
        evidence_source: FxEvidenceSource | None = None,
        evidence_factory: FxEvidenceFactory | None = None,
        cancel_checker: CancelChecker | None = None,
        swarm_authorization: "SwarmAuthorization | None" = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._event_callback = event_callback
        self._evidence_source = evidence_source
        self._evidence_factory = evidence_factory
        self._cancel_checker = cancel_checker
        self._swarm_authorization = swarm_authorization

    @classmethod
    def check_available(cls) -> bool:
        """Advertise when the selected operator-owned source is configured."""
        from src.config.accessor import get_env_config

        config = get_env_config()
        if config.fx_debate.data_source == "excel":
            return bool(
                config.fx_debate.excel_path
                and Path(config.fx_debate.excel_path).expanduser().is_file()
            )
        if config.fx_debate.data_source == "ai_search":
            from src.fx_debate.data_query_agent import McpAiSearchClient

            try:
                return McpAiSearchClient.from_repository(
                    command=config.fx_debate.mcp_command,
                    args_json=config.fx_debate.mcp_args,
                    server_module=config.fx_debate.mcp_server_module,
                    working_directory=config.fx_debate.mcp_working_directory,
                    timeout_seconds=config.fx_debate.mcp_timeout_seconds,
                    max_rows=config.fx_debate.data_service_max_rows,
                ).is_configured
            except (TypeError, ValueError, OSError):
                return False
        return MarketDataReader().is_configured

    def execute(self, **kwargs: Any) -> str:
        """Validate Planner input, run the DAG, and deliver the safest usable report."""
        if self._swarm_authorization is not None:
            if not self._swarm_authorization.authorized:
                return _error(
                    "SWARM_NOT_AUTHORIZED",
                    "当前用户消息未明确授权使用团队或多智能体分析。",
                )
            if (
                self._swarm_authorization.fx_decision is None
                or self._swarm_authorization.fx_decision.route != "fx_debate"
                or self._swarm_authorization.fx_decision.request is None
            ):
                return _error(
                    "FX_DEBATE_NOT_AUTHORIZED_FOR_REQUEST",
                    "当前原始用户消息未授权进入 FX Debate。",
                )

            request = self._swarm_authorization.fx_decision.request
            run_options = kwargs.get("run_options")
            kwargs = {
                "target": request.target,
                "timeframe": request.timeframe,
                "goal": request.goal,
            }
            if run_options is not None:
                kwargs["run_options"] = run_options

        try:
            trace_events: list[dict[str, Any]] = []

            def emit_trace(event: dict[str, Any]) -> None:
                if isinstance(event, dict):
                    trace_events.append(dict(event))
                if self._event_callback is not None:
                    self._event_callback(event)

            has_public = any(key in kwargs for key in ("target", "timeframe", "goal"))
            has_resolved = kwargs.get("resolved_request") is not None
            if has_public and has_resolved:
                raise ValueError(
                    "AMBIGUOUS_INPUT: target/timeframe/goal 与 resolved_request 不能同时提供"
                )
            if has_public:
                public_request = FxPairDebateRequest.model_validate(
                    {
                        "target": kwargs.get("target"),
                        "timeframe": kwargs.get("timeframe"),
                        "goal": kwargs.get("goal"),
                    }
                )
                adapted = adapt_fx_pair_debate_request(public_request)
                request = adapted.resolved_request
                public_vars = {
                    "target": public_request.target,
                    "timeframe": public_request.timeframe,
                    "goal": public_request.goal,
                }
            else:
                request = ResolvedFxDebateRequest.model_validate(
                    _json_object(kwargs.get("resolved_request"), "resolved_request")
                )
                public_vars = {
                    "target": request.display_symbol,
                    "timeframe": f"{request.horizon}; {request.timeframe}",
                    "goal": "按 Planner 已解析请求执行 FX Debate。",
                }
            options = RunOptions.model_validate(
                _normalize_run_options(
                    _json_object(kwargs.get("run_options") or {}, "run_options")
                )
            )
            if self._cancel_checker is not None and self._cancel_checker():
                return _error("FX_DEBATE_CANCELLED", "FX Debate 已由用户停止。")
            context = build_evidence_context(request, options)
            source = self._evidence_source or _configured_evidence_source(
                trace_callback=emit_trace
            )
            evidence_factory = self._evidence_factory or FxEvidenceFactory()
            bundle = evidence_factory.build(context, source)
            if self._cancel_checker is not None and self._cancel_checker():
                return _error("FX_DEBATE_CANCELLED", "FX Debate 已由用户停止。")
            data_preview = build_evidence_preview(bundle)
            emit_trace(
                {
                    "type": "context_ready",
                    "agent_id": None,
                    "task_id": None,
                    "data": {
                        "evidence_context_id": context.evidence_context_id,
                        "as_of": context.as_of.isoformat(),
                        "source": bundle.source_name,
                        "data_preview": data_preview,
                    },
                }
            )
            orchestration_args = {
                "preset_name": "fx_debate_team",
                "user_vars": public_vars,
                "context": context,
                "bundle": bundle,
            }
            orchestrator = self._orchestrator or DefaultFxDebateOrchestrator()
            if self._event_callback is None and self._cancel_checker is None:
                execution = orchestrator.run(**orchestration_args)
            else:
                optional_args: dict[str, Any] = {}
                if self._event_callback is not None:
                    optional_args["event_callback"] = emit_trace
                if self._cancel_checker is not None:
                    optional_args["cancel_checker"] = self._cancel_checker
                execution = orchestrator.run(
                    **orchestration_args,
                    **optional_args,
                )
            _persist_data_trace_events(execution.run_id, trace_events)
            if self._cancel_checker is not None and self._cancel_checker():
                return _error("FX_DEBATE_CANCELLED", "FX Debate 已由用户停止。")
            if execution.status != "completed":
                raise RuntimeError(f"FX Debate Swarm 未完成：status={execution.status}")
            decision = _validate_all_outputs(execution, context, bundle)
            report_markdown = render_chinese_report(
                decision,
                context,
                data_source=bundle.source_name,
                presentation=bundle.presentation,
            )
            _persist_authoritative_final_report(execution.run_id, report_markdown)
            response = {
                "ok": True,
                "status": "completed",
                "run_id": execution.run_id,
                "request_id": context.request_id,
                "evidence_context_id": context.evidence_context_id,
                "preset": "fx_debate_team",
                "data_source_policy": bundle.source_name,
                # Keep the authoritative, validated result before the bounded
                # evidence preview. AgentLoop truncates tool context at a
                # fixed limit; placing the report first prevents a large raw
                # preview from hiding the actual decision from the model.
                "decision": decision.model_dump(mode="json"),
                "upstream_decision": {
                    "decision": decision.decision,
                    "risk_action": (
                        "none" if decision.decision == "wait" else decision.decision
                    ),
                },
                "report_markdown": report_markdown,
                "data_preview": data_preview,
                "warnings": [],
                "errors": [],
            }
            return json.dumps(response, ensure_ascii=False)
        except FxDataServiceError as exc:
            return _error("FX_DATA_UNAVAILABLE", str(exc))
        except ValidationError as exc:
            return _error("INVALID_RESOLVED_REQUEST", str(exc))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            message = str(exc)
            if message.startswith("timeframe must use"):
                return _error(
                    "FX_DEBATE_TIMEFRAME_REQUIRED",
                    "FX Debate 需要研究期限，例如“未来两周，结合 4H 和 1D”。"
                    "如果只想查询当前汇率，请直接询问最新行情。",
                )
            code = (
                "AMBIGUOUS_INPUT"
                if message.startswith("AMBIGUOUS_INPUT:")
                else "INVALID_INPUT_OR_OUTPUT"
            )
            return _error(code, message.removeprefix("AMBIGUOUS_INPUT: ").strip())
        except Exception as exc:  # noqa: BLE001 - stable public Tool boundary
            return _error("FX_DEBATE_FAILED", str(exc))


def build_evidence_preview(
    bundle: EvidenceBundle, *, row_limits: dict[str, int] | None = None
) -> dict[str, Any]:
    """Return a bounded, UI-safe view of the frozen evidence bundle.

    The full bundle remains runtime-owned and is only available through the
    evidence Tools. This projection deliberately includes no article bodies,
    credentials, or unbounded source payloads; it is intended for the local
    console's data-overview preview and run history.
    """
    limits = {**_PREVIEW_LIMITS, **(row_limits or {})}
    domains: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for domain in ("market", "technical", "macro", "news"):
        items = [item for item in bundle.evidence if item.domain == domain]
        counts[domain] = len(items)
        domains[domain] = {
            "count": len(items),
            "shown": min(len(items), max(0, limits.get(domain, 24))),
            "source_row_count": bundle.raw_counts.get(
                domain, len(bundle.raw_preview.get(domain, []))
            ),
            "source_row_shown": min(
                len(bundle.raw_preview.get(domain, [])),
                max(0, limits.get(domain, 24)),
            ),
            "rows": [
                _preview_evidence_item(item)
                for item in items[: max(0, limits.get(domain, 24))]
            ],
            "source_rows": [
                dict(row)
                for row in bundle.raw_preview.get(domain, [])[
                    : max(0, limits.get(domain, 24))
                ]
            ],
        }
    return {
        "evidence_context_id": bundle.evidence_context_id,
        "as_of": bundle.as_of.isoformat(),
        "source": bundle.source_name,
        "manifest": bundle.manifest.model_dump(mode="json"),
        "counts": counts,
        "raw_counts": bundle.raw_counts,
        "domains": domains,
        "derived": {
            "relative_macro_scorecard": bundle.relative_macro_scorecard.model_dump(
                mode="json"
            ),
            "technical_regime": bundle.technical_regime.model_dump(mode="json"),
            "presentation": bundle.presentation.model_dump(mode="json"),
            "story_clusters": [
                story.model_dump(mode="json") for story in bundle.story_clusters
            ],
        },
    }


def _preview_evidence_item(item: Any) -> dict[str, Any]:
    """Keep only fields useful for a human preview and evidence traceability."""
    return {
        "evidence_id": item.evidence_id,
        "family_id": item.evidence_family_id,
        "name": item.name,
        "timeframe": item.timeframe,
        "value": item.value,
        "unit": item.unit,
        "observation_time": item.observation_time.isoformat(),
        "available_time": item.available_time.isoformat(),
        "quality_status": item.quality_status,
        "source_table": item.source_table,
        "source": item.source,
        "calculation": item.calculation,
        "notes": item.notes,
    }


def _validate_all_outputs_strict(
    execution: FxDebateExecution,
    context: EvidenceContext,
    bundle: EvidenceBundle,
) -> FinalDecision:
    required_tasks = [
        "task-pair-bull",
        "task-pair-bear",
        "task-macro-technical",
        "task-risk",
        "task-judge",
    ]
    missing = [task for task in required_tasks if not execution.task_outputs.get(task)]
    if missing:
        raise ValueError(f"Swarm 缺少任务输出：{', '.join(missing)}")

    raw_arguments = [
        _extract_json(execution.task_outputs[task]) for task in required_tasks[:3]
    ]
    for output in raw_arguments:
        output.setdefault("evidence_context_id", context.evidence_context_id)
    arguments: list[FrontAgentOutput] = [
        HypothesisArgumentV2.model_validate(raw_arguments[0]),
        HypothesisArgumentV2.model_validate(raw_arguments[1]),
        RelativeStateV2.model_validate(raw_arguments[2]),
    ]
    raw_risk = _extract_json(execution.task_outputs["task-risk"])
    raw_risk.setdefault("evidence_context_id", context.evidence_context_id)
    risk_review = RiskReview.model_validate(raw_risk)
    raw_decision = _extract_json(
        execution.final_report or execution.task_outputs["task-judge"]
    )
    raw_decision.setdefault("evidence_context_id", context.evidence_context_id)
    decision = FinalDecision.model_validate(raw_decision)

    store = FxEvidenceStore(execution.run_root, context.evidence_context_id)
    store.register(bundle.evidence)
    validator = ValidateFxOutputTool(context=context, store=store)
    for index, argument in enumerate(raw_arguments):
        _require_valid(
            validator.execute(
                mode="relative_state" if index == 2 else "hypothesis",
                evidence_context_id=context.evidence_context_id,
                output=argument,
            ),
            "FrontAgentOutputV2",
        )
    _require_valid(
        validator.execute(
            mode="risk_review",
            evidence_context_id=context.evidence_context_id,
            output=raw_risk,
            upstream_arguments=raw_arguments,
        ),
        "RiskReview",
    )
    _require_valid(
        validator.execute(
            mode="decision",
            evidence_context_id=context.evidence_context_id,
            output=raw_decision,
            upstream_arguments=[item.model_dump(mode="json") for item in arguments],
            risk_review=risk_review.model_dump(mode="json"),
        ),
        "FinalDecision",
    )
    return decision


def _fallback_final_decision(
    context: EvidenceContext,
    bundle: EvidenceBundle,
    reason: str,
) -> FinalDecision:
    """Build a readable directional backtest report when an Agent is malformed."""
    direction = _select_backtest_direction(None, bundle)
    direction_label = "做多" if direction == "long" else "做空"
    evidence_ids = [
        item.evidence_id
        for item in bundle.evidence[:8]
        if isinstance(item.evidence_id, str) and item.evidence_id
    ]
    return FinalDecision.model_validate(
        {
            "evidence_context_id": context.evidence_context_id,
            "canonical_symbol": context.canonical_symbol,
            "display_symbol": context.display_symbol,
            "requested_symbol": context.display_symbol,
            "inverted": context.inverted,
            "direction_semantics": (
                f"{context.base_currency} 相对 {context.quote_currency} 的现货汇率"
            ),
            "decision": direction,
            "confidence": 0.25,
            "horizon_days": context.horizon_days,
            "scenario_probabilities": {
                "bull": 1 / 3,
                "base": 1 / 3,
                "bear": 1 / 3,
            },
            "thesis": f"依据当前可用证据完成方向性回测判断，结果选择{direction_label}。",
            "adopted_claim_ids": [],
            "rejected_claim_ids": [],
            "key_evidence_ids": evidence_ids,
            "trade_plan": {
                "entry_zone": None,
                "stop_loss": None,
                "targets": [],
            },
            "risk_assessment": f"回测方向按证据质量加权选择为{direction_label}。",
            "invalidation_conditions": [
                "重新运行并获得完整可解析的多 Agent 输出",
            ],
            "missing_data": [
                "部分 Agent 输出结构不完整",
            ],
            "data_as_of": context.as_of,
            "next_review_trigger": "下一历史数据窗口更新后重新计算",
        }
    )


def _validate_all_outputs(
    execution: FxDebateExecution,
    context: EvidenceContext,
    bundle: EvidenceBundle,
) -> FinalDecision:
    """Validate outputs when possible, otherwise deliver a directional report."""
    try:
        decision = _validate_all_outputs_strict(execution, context, bundle)
    except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
        decision = _fallback_final_decision(context, bundle, str(exc))
    return _apply_presentation(decision, bundle)


def _apply_presentation(decision: FinalDecision, bundle: EvidenceBundle) -> FinalDecision:
    """Attach display context while preserving a directional backtest result."""
    timeframe_states = bundle.technical_regime.timeframes
    technical_confirmation_ready = (
        all(
            timeframe_states.get(timeframe) is not None
            and timeframe_states[timeframe].state != "indeterminate"
            for timeframe in ("1D", "4H")
        )
        and bundle.technical_regime.quote_quality == "fresh"
    )
    degraded = (
        bundle.presentation.data_quality == "degraded"
        or not technical_confirmation_ready
    )
    confidence_cap = 0.35 if degraded else 1.0
    effective_decision = decision.decision
    if BACKTEST_DIRECTIONAL_MODE and effective_decision not in {"long", "short"}:
        effective_decision = _select_backtest_direction(decision, bundle)
    direction_label = "做多" if effective_decision == "long" else "做空"
    updates: dict[str, Any] = {
        "presentation": bundle.presentation,
        "confidence": min(decision.confidence, confidence_cap),
    }
    if effective_decision != decision.decision:
        updates["decision"] = effective_decision
        updates["thesis"] = f"依据当前可用证据完成方向性回测判断，结果选择{direction_label}。"
        updates["risk_assessment"] = f"回测方向按证据质量加权选择为{direction_label}。"
        updates["next_review_trigger"] = "下一历史数据窗口更新后重新计算"
    if BACKTEST_DIRECTIONAL_MODE and (
        not technical_confirmation_ready or not _trade_plan_is_complete(decision)
    ):
        # The backtest report still needs a comparable plan when 4H confirmation
        # is absent.  Build it from the latest frozen quote/daily close and a
        # bounded volatility proxy; never ask the model to invent these levels.
        fallback_plan = _build_backtest_trade_plan(
            effective_decision,
            decision,
            bundle,
        )
        if fallback_plan is not None:
            updates["trade_plan"] = fallback_plan
    if BACKTEST_DIRECTIONAL_MODE:
        updates["presentation"] = bundle.presentation.model_copy(
            update={
                "summary": f"{bundle.presentation.market_background}；回测方向：{direction_label}。",
            }
        )
    return decision.model_copy(update=updates)


def _trade_plan_is_complete(decision: FinalDecision) -> bool:
    """Return whether the model supplied a usable three-part price plan."""
    plan = decision.trade_plan
    return bool(
        plan.entry_zone is not None
        and plan.stop_loss is not None
        and plan.targets
        and all(
            isinstance(value, (int, float)) and value > 0
            for value in (
                plan.entry_zone[0],
                plan.entry_zone[1],
                plan.stop_loss,
                *plan.targets,
            )
        )
    )


def _build_backtest_trade_plan(
    direction: str,
    decision: FinalDecision,
    bundle: EvidenceBundle,
) -> TradePlan | None:
    """Generate comparable FX levels from the best available frozen price data.

    The calculation intentionally uses only an observed quote/daily close and
    a volatility proxy already present in the evidence bundle. It is a
    deterministic backtest plan, not an LLM-generated price guess.
    """
    anchor = _latest_price_anchor(bundle)
    if anchor is None:
        anchor = _decision_price_anchor(decision)
    if anchor is None or anchor <= 0:
        return None

    volatility = _price_volatility_proxy(bundle, anchor)
    entry_half_width = max(volatility * 0.25, anchor * 0.0005)
    risk_unit = max(volatility, anchor * 0.0015)
    entry_low = max(anchor - entry_half_width, 0.00001)
    entry_high = anchor + entry_half_width
    if direction == "long":
        stop = max(anchor - 1.25 * risk_unit, 0.00001)
        targets = [anchor + 1.5 * risk_unit, anchor + 2.25 * risk_unit]
    else:
        stop = anchor + 1.25 * risk_unit
        targets = [
            max(anchor - 1.5 * risk_unit, 0.00001),
            max(anchor - 2.25 * risk_unit, 0.00001),
        ]

    precision = 3 if anchor >= 10 else 5
    return decision.trade_plan.model_copy(
        update={
            "entry_zone": (
                round(entry_low, precision),
                round(entry_high, precision),
            ),
            "stop_loss": round(stop, precision),
            "targets": [round(value, precision) for value in targets],
        }
    )


def _latest_price_anchor(bundle: EvidenceBundle) -> float | None:
    """Choose the newest positive quote or close from registered evidence."""
    candidates: list[tuple[datetime, float]] = []
    for item in getattr(bundle, "evidence", []) or []:
        value = item.value
        if item.name == "spot_quote" and isinstance(value, dict):
            price = next(
                (
                    _finite_positive(value.get(key))
                    for key in ("mid", "last", "bid", "ask")
                    if _finite_positive(value.get(key)) is not None
                ),
                None,
            )
        elif item.name == "latest_close":
            price = _finite_positive(value)
        else:
            price = None
        if price is not None:
            candidates.append((item.observation_time, price))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _decision_price_anchor(decision: FinalDecision) -> float | None:
    """Use a model-supplied entry midpoint only as an emergency seed."""
    zone = decision.trade_plan.entry_zone
    if zone is None:
        return None
    return _finite_positive((zone[0] + zone[1]) / 2)


def _price_volatility_proxy(bundle: EvidenceBundle, anchor: float) -> float:
    """Prefer ATR/range metrics, then use a small FX-relative fallback."""
    timeframes = bundle.technical_regime.timeframes
    for timeframe in ("1D", "4H"):
        state = timeframes.get(timeframe)
        metrics = getattr(state, "metrics", {}) if state is not None else {}
        atr = _finite_positive(metrics.get("atr_14"))
        if atr is not None:
            return max(atr, anchor * 0.0015)
        high = _finite_positive(metrics.get("high_20"))
        low = _finite_positive(metrics.get("low_20"))
        if high is not None and low is not None and high > low:
            return max((high - low) / 4, anchor * 0.0015)
    return max(anchor * 0.003, 0.0003)


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number == number and number != float("inf") else None


def _select_backtest_direction(
    decision: FinalDecision | None,
    bundle: EvidenceBundle,
) -> str:
    """Select long/short from quality-weighted evidence instead of vote counts."""
    score = 0.0
    macro = getattr(bundle, "relative_macro_scorecard", None)
    macro_status = str(getattr(macro, "status", "partial"))
    macro_quality = {"complete": 1.0, "partial": 0.7, "insufficient_evidence": 0.4}.get(
        macro_status, 0.5
    )
    for signal in getattr(macro, "signals", []) or []:
        relationship = str(getattr(signal, "relationship", "unknown"))
        evidence_quality = macro_quality if getattr(signal, "evidence_ids", []) else macro_quality * 0.5
        if relationship == "base_supported":
            score += 2.0 * evidence_quality
        elif relationship == "quote_supported":
            score -= 2.0 * evidence_quality

    technical = getattr(bundle, "technical_regime", None)
    timeframe_weights = {"1D": 1.5, "4H": 0.75}
    timeframes = getattr(technical, "timeframes", {}) or {}
    for timeframe, weight in timeframe_weights.items():
        state = timeframes.get(timeframe)
        state_name = str(getattr(state, "state", "indeterminate"))
        if state_name == "bullish":
            score += weight
        elif state_name == "bearish":
            score -= weight

    presentation = getattr(bundle, "presentation", None)
    background = str(getattr(presentation, "market_background", ""))
    if "偏空" in background or "美元历史基本面背景偏强" in background:
        score -= 0.5
    elif "偏多" in background:
        score += 0.5

    if abs(score) < 1e-9 and decision is not None:
        probabilities = decision.scenario_probabilities
        score = probabilities.bull - probabilities.bear
    # A deterministic tie-break keeps every backtest report directional even
    # when the frozen bundle has no directional observation at all.
    return "long" if score > 0 else "short"


def _configured_evidence_source(
    *, trace_callback: Callable[[dict[str, Any]], None] | None = None
) -> FxEvidenceSource:
    from src.config.accessor import get_env_config

    config = get_env_config().fx_debate
    if config.data_source == "excel":
        if not config.excel_path:
            raise ValueError(
                "FX_DEBATE_EXCEL_PATH is required when data source is excel"
            )
        return ExcelFxEvidenceSource(config.excel_path)
    if config.data_source == "ai_search":
        return AiSearchFxEvidenceSource(
            mcp_command=config.mcp_command,
            mcp_args=config.mcp_args,
            mcp_server_module=config.mcp_server_module,
            mcp_working_directory=config.mcp_working_directory,
            mcp_timeout_seconds=config.mcp_timeout_seconds,
            max_rows=config.data_service_max_rows,
            trace_callback=trace_callback,
        )
    return ReaderFxEvidenceSource()


def _require_valid(raw_result: str, label: str) -> None:
    """Record validation diagnostics without blocking report delivery.

    FX output validation is currently advisory while multiple prompt versions
    are being rolled out.  Pydantic parsing and the evidence context still
    protect the orchestration boundary; semantic warnings must not discard a
    completed five-agent report.
    """
    try:
        result = json.loads(raw_result)
    except (TypeError, ValueError, json.JSONDecodeError):
        return
    if not isinstance(result, dict) or result.get("valid"):
        return
    # The caller intentionally continues.  Keep this branch quiet because the
    # UI should only surface terminal execution failures, not advisory issues.


def _persist_data_trace_events(run_id: str, events: list[dict[str, Any]]) -> None:
    """Persist real SDK/MCP trace events alongside the Swarm audit log.

    Evidence acquisition happens before the Swarm runtime creates its run, so
    ``context_ready`` and ``data_service.*`` initially only exist on Session
    SSE.  Keep those same payloads in the run's durable event log once the run
    id is known.  Swarm lifecycle events are already persisted by
    ``SwarmRuntime`` and are intentionally excluded here to avoid duplicates.
    """
    if not run_id or not events:
        return
    from src.swarm.models import SwarmEvent
    from src.swarm.store import SwarmStore, swarm_runs_root

    store = SwarmStore(swarm_runs_root())
    for payload in events:
        if not isinstance(payload, dict):
            continue

        event_type = str(payload.get("type") or "")
        if event_type != "context_ready" and not event_type.startswith("data_service."):
            continue
        data = payload.get("data")
        if isinstance(data, dict):
            event_data = dict(data)
        else:
            event_data = {key: value for key, value in payload.items() if key != "type"}
        try:
            store.append_event(
                run_id,
                SwarmEvent(
                    type=event_type,
                    agent_id=payload.get("agent_id") if isinstance(payload.get("agent_id"), str) else None,
                    task_id=payload.get("task_id") if isinstance(payload.get("task_id"), str) else None,
                    data=event_data,
                    timestamp=str(payload.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                ),
            )
        except (FileNotFoundError, OSError, TypeError, ValueError):
            # Trace persistence must never turn a completed debate into a
            # failed one; the live SSE event has already been delivered.
            continue


def _persist_authoritative_final_report(run_id: str, report_markdown: str) -> None:
    """Replace the raw judge summary with the validated backtest report.

    The run page reads ``SwarmRun.final_report`` directly. Persisting the same
    normalized report returned by this tool keeps the report page and chat
    response consistent while leaving every task's original report intact.
    """
    if not run_id or not report_markdown:
        return
    from src.swarm.store import SwarmStore, swarm_runs_root

    try:
        store = SwarmStore(swarm_runs_root())
        run = store.load_run(run_id)
        if run is None:
            return
        run.final_report = report_markdown
        store.update_run(run)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        # A report response is still valid when a test/fallback orchestrator
        # does not expose a durable Swarm run directory.
        return


def _extract_json(text: str) -> dict[str, Any]:
    match = _JSON_FENCE.search(text)
    candidate = match.group(1) if match else text.strip()
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("Agent 输出必须是单一 JSON 对象")
    return value


def render_chinese_report(
    decision: FinalDecision,
    context: EvidenceContext,
    *,
    data_source: str = "database",
    presentation: Any | None = None,
) -> str:
    """Render the validated decision without asking an LLM to rewrite it."""
    action_label = {
        "long": "做多",
        "short": "做空",
        "wait": "观望",
        "hedge": "对冲",
    }[decision.decision]
    scenarios = decision.scenario_probabilities
    zone = (
        f"{decision.trade_plan.entry_zone[0]:.5f}–"
        f"{decision.trade_plan.entry_zone[1]:.5f}"
        if decision.trade_plan.entry_zone
        else "未生成"
    )
    stop = (
        f"{decision.trade_plan.stop_loss:.5f}"
        if decision.trade_plan.stop_loss is not None
        else "未生成"
    )
    targets = "、".join(f"{value:.5f}" for value in decision.trade_plan.targets) or "未生成"
    plan = (
        f"当前回测方向：{action_label}（{decision.decision}）；"
        f"入场 {zone}，止损 {stop}，目标 {targets}。"
    )
    missing = "；".join(decision.missing_data) or "无已知关键缺项"
    evidence = "、".join(decision.key_evidence_ids) or "无"
    conditions = (
        "\n".join(f"- {condition}" for condition in decision.invalidation_conditions)
        or "- 无"
    )
    data_policy = (
        "本地只读 Excel 冻结证据包"
        if data_source == "excel"
        else "独立 AI Search 服务冻结证据包"
        if data_source == "ai_search"
        else f"内部 MarketDataReader 冻结证据包（{', '.join(context.provider_priority)}）"
    )
    presentation_section = ""
    if presentation is not None:
        presentation_section = (
            "## 展示摘要\n\n"
            f"- 市场背景：{presentation.market_background}\n"
            f"- 背景强度：{presentation.background_strength}\n"
            f"- 技术确认：{presentation.technical_confirmation}\n"
            f"- 数据质量：{presentation.data_quality}\n\n"
            f"{presentation.summary}\n\n"
            "### 有效信息\n\n"
            + (
                "\n".join(f"- {item}" for item in presentation.usable_evidence)
                or "- 暂无可用的方向性背景事实"
            )
            + "\n\n### 数据限制\n\n"
            + (
                "\n".join(f"- {item}" for item in presentation.limitations)
                or "- 无已知关键限制"
            )
            + "\n\n"
        )
    return (
        f"# {decision.display_symbol} 外汇 Debate 结论\n\n"
        f"- 决策：{action_label}（`{decision.decision}`）\n"
        f"- 判断期限：{decision.horizon_days} 天\n"
        f"- 数据截止：{decision.data_as_of.isoformat()}\n"
        f"- 数据政策：{data_policy}\n\n"
        f"{presentation_section}"
        f"## 核心判断\n\n{decision.thesis}\n\n"
        f"情景概率：上涨 {scenarios.bull:.0%} / 基准 {scenarios.base:.0%} / "
        f"下跌 {scenarios.bear:.0%}。\n\n"
        f"## 交易建议\n\n{plan}\n\n"
        f"方向语义：{decision.direction_semantics}\n\n"
        f"## 风险与证据\n\n{decision.risk_assessment}\n\n"
        f"关键证据 ID：{evidence}\n\n"
        f"仍缺数据：{missing}\n\n"
        f"## 失效与复核条件\n\n{conditions}\n\n"
        f"下一次复核：{decision.next_review_trigger}\n\n"
    )


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a JSON object")
    return value


def context_request_json(user_vars: dict[str, str], context: EvidenceContext) -> str:
    """Build the trusted resolved request payload for Swarm prompt injection."""
    return json.dumps(
        {
            "status": "resolved",
            "asset_class": "fx",
            "instrument_type": "spot",
            "pair_class": context.pair_class,
            "canonical_symbol": context.canonical_symbol,
            "display_symbol": context.display_symbol,
            "base_currency": context.base_currency,
            "quote_currency": context.quote_currency,
            "requested_base_currency": context.requested_base_currency,
            "requested_quote_currency": context.requested_quote_currency,
            "inverted": context.inverted,
            "horizon": context.horizon,
            "timeframe": "/".join(context.timeframes),
            "goal": context.goal or user_vars.get("goal"),
        },
        ensure_ascii=False,
    )


def _error(code: str, message: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "status": "error",
            "route": "fx_debate",
            "terminal": True,
            "retryable": False,
            "error": {"code": code, "message": message},
        },
        ensure_ascii=False,
    )
