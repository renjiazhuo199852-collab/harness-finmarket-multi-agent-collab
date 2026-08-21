"""Deterministic structure, evidence, and risk validator for FX Debate."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from pydantic import ValidationError

from src.fx_debate.contracts import (
    AgentArgument,
    FinalDecision,
    FrontAgentOutput,
    HypothesisArgumentV2,
    RelativeStateV2,
    RiskReview,
    ValidationIssue,
    ValidationResult,
    claims_view,
)
from src.fx_debate.models import EvidenceContext, EvidenceItem
from src.fx_debate.store import FxEvidenceStore
from src.tools.fx_debate_tools import _FxContextTool

_Mode = Literal["argument", "hypothesis", "relative_state", "risk_review", "decision"]
_REQUIRED_DATA_TOOLS = {
    "get_fx_market_evidence",
    "get_fx_macro_evidence",
    "get_fx_news_evidence",
}
_REQUIRED_BUNDLE_TOOLS = {
    "get_fx_evidence_manifest",
    "get_fx_relative_macro_scorecard",
    "get_fx_technical_regime",
}
_RISK_CAPS = {"conservative": 0.25, "balanced": 0.5, "aggressive": 1.0}


class ValidateFxOutputTool(_FxContextTool):
    """Validate Agent JSON without generating or rewriting its conclusions."""

    name = "validate_fx_output"
    description = (
        "校验 HypothesisArgumentV2、RelativeStateV2、历史 AgentArgument、"
        "RiskReview 或 FinalDecision 的结构、Evidence Context、证据引用和 Risk 约束；"
        "返回可据此修订的 errors/warnings，但不替 Agent 补写结论。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    "argument",
                    "hypothesis",
                    "relative_state",
                    "risk_review",
                    "decision",
                ],
            },
            "evidence_context_id": {"type": "string"},
            "output": {"type": "object"},
            "upstream_arguments": {
                "type": "array",
                "items": {"type": "object"},
            },
            "risk_review": {"type": "object"},
        },
        "required": ["mode", "evidence_context_id", "output"],
    }
    repeatable = True

    @classmethod
    def check_available(cls) -> bool:
        """Validation reads the frozen EvidenceBundle and is source-agnostic."""
        return True

    def __init__(
        self,
        *,
        context: EvidenceContext | None = None,
        store: FxEvidenceStore | None = None,
    ) -> None:
        super().__init__(context=context, store=store)

    def execute(self, **kwargs: Any) -> str:
        """Return structured validation errors and checked evidence IDs."""
        raw_mode = str(kwargs.get("mode") or "")
        mode = cast(
            _Mode,
            (
                raw_mode
                if raw_mode
                in {
                    "argument",
                    "hypothesis",
                    "relative_state",
                    "risk_review",
                    "decision",
                }
                else "argument"
            ),
        )
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        checked: list[str] = []
        try:
            if raw_mode != mode:
                raise ValueError(
                    "mode must be argument, hypothesis, relative_state, risk_review, or decision"
                )
            context, store = self._resources(kwargs)
            raw_output = _object(kwargs.get("output"), "output")
            # Some model responses omit the repeated context field even though
            # the Tool call carries it as a required argument. Fill only this
            # non-semantic envelope field; evidence and risk checks stay strict.
            if not raw_output.get("evidence_context_id"):
                raw_output["evidence_context_id"] = str(
                    kwargs.get("evidence_context_id") or ""
                )
            model = _validate_contract(mode, raw_output)
            _check_context(
                model.evidence_context_id, context, errors, "$.evidence_context_id"
            )

            upstream = _upstream_arguments(kwargs, mode, context, errors)
            risk_review = _risk_review(kwargs, mode, context, errors)
            evidence_ids = _evidence_ids(model)
            checked, checked_items = _check_evidence(
                evidence_ids, context, store, errors
            )

            if isinstance(model, AgentArgument):
                _check_argument(model, errors)
            elif isinstance(model, HypothesisArgumentV2):
                _check_hypothesis(model, checked_items, store.list_all(), errors)
            elif isinstance(model, RelativeStateV2):
                _check_relative_state(model, checked_items, errors)
            elif isinstance(model, RiskReview):
                _check_risk_review(model, upstream, context, errors)
            else:
                _check_decision(model, upstream, risk_review, context, errors)
        except ValidationError as exc:
            errors.extend(_schema_issues(exc))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(
                ValidationIssue(
                    code="INVALID_REQUEST",
                    path="",
                    message=str(exc),
                )
            )
        result = ValidationResult(
            valid=not errors,
            mode=mode,
            errors=errors,
            warnings=warnings,
            checked_evidence_ids=checked,
        )
        return result.model_dump_json()


def _validate_contract(
    mode: _Mode, output: dict[str, Any]
) -> FrontAgentOutput | RiskReview | FinalDecision:
    if mode == "argument":
        return AgentArgument.model_validate(output)
    if mode == "hypothesis":
        return HypothesisArgumentV2.model_validate(output)
    if mode == "relative_state":
        return RelativeStateV2.model_validate(output)
    if mode == "risk_review":
        return RiskReview.model_validate(output)
    return FinalDecision.model_validate(output)


def _upstream_arguments(
    kwargs: dict[str, Any],
    mode: _Mode,
    context: EvidenceContext,
    errors: list[ValidationIssue],
) -> list[FrontAgentOutput]:
    if mode in {"argument", "hypothesis", "relative_state"}:
        return []
    raw = kwargs.get("upstream_arguments")
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError("risk_review/decision requires exactly 3 upstream_arguments")
    arguments = [_parse_front_output(_object(item, "argument")) for item in raw]
    for index, argument in enumerate(arguments):
        _check_context(
            argument.evidence_context_id,
            context,
            errors,
            f"$.upstream_arguments[{index}].evidence_context_id",
        )
    return arguments


def _risk_review(
    kwargs: dict[str, Any],
    mode: _Mode,
    context: EvidenceContext,
    errors: list[ValidationIssue],
) -> RiskReview | None:
    if mode != "decision":
        return None
    review = RiskReview.model_validate(
        _object(kwargs.get("risk_review"), "risk_review")
    )
    _check_context(
        review.evidence_context_id,
        context,
        errors,
        "$.risk_review.evidence_context_id",
    )
    return review


def _evidence_ids(
    model: FrontAgentOutput | RiskReview | FinalDecision,
) -> list[str]:
    if isinstance(model, AgentArgument):
        values = [
            evidence_id for claim in model.claims for evidence_id in claim.evidence_ids
        ]
        values.extend(
            evidence_id
            for counter in model.counter_evidence
            for evidence_id in counter.evidence_ids
        )
    elif isinstance(model, HypothesisArgumentV2):
        values = [
            evidence_id
            for chain in model.causal_chains
            for evidence_id in chain.evidence_ids
        ]
        for statements in (
            model.catalysts,
            model.market_confirmations,
            model.strongest_countercase,
        ):
            values.extend(
                evidence_id
                for statement in statements
                for evidence_id in statement.evidence_ids
            )
    elif isinstance(model, RelativeStateV2):
        values = [
            evidence_id
            for finding in model.findings
            for evidence_id in finding.evidence_ids
        ]
    elif isinstance(model, RiskReview):
        values = [
            evidence_id
            for conflict in model.evidence_conflicts
            for evidence_id in conflict.evidence_ids
        ]
        values.extend(
            evidence_id
            for group in model.duplicate_claim_groups
            for evidence_id in group.shared_evidence_ids
        )
    else:
        values = model.key_evidence_ids
    return list(dict.fromkeys(values))


def _check_evidence(
    evidence_ids: list[str],
    context: EvidenceContext,
    store: FxEvidenceStore,
    errors: list[ValidationIssue],
) -> tuple[list[str], list[EvidenceItem]]:
    evidence, missing = store.get(evidence_ids)
    for evidence_id in missing:
        errors.append(
            ValidationIssue(
                code="EVIDENCE_NOT_FOUND",
                path="$.evidence_ids",
                message=f"证据 {evidence_id!r} 未在当前 Context 登记。",
            )
        )
    checked: list[str] = []
    for item in evidence:
        checked.append(item.evidence_id)
        if item.evidence_context_id != context.evidence_context_id:
            errors.append(
                ValidationIssue(
                    code="EVIDENCE_CONTEXT_MISMATCH",
                    path="$.evidence_ids",
                    message=f"证据 {item.evidence_id!r} 属于其他 Context。",
                )
            )
        if item.available_time > context.as_of:
            errors.append(
                ValidationIssue(
                    code="FUTURE_EVIDENCE",
                    path="$.evidence_ids",
                    message=f"证据 {item.evidence_id!r} 在 as_of 后才可获得。",
                )
            )
    return checked, evidence


def _check_argument(argument: AgentArgument, errors: list[ValidationIssue]) -> None:
    if argument.analysis_status == "complete":
        called = {trace.tool_name for trace in argument.tool_calls}
        missing = sorted(_REQUIRED_DATA_TOOLS - called)
        if missing:
            errors.append(
                ValidationIssue(
                    code="MISSING_REQUIRED_TOOL_CALL",
                    path="$.tool_calls",
                    message=f"完整分析缺少必查 Tool：{', '.join(missing)}。",
                )
            )


def _check_hypothesis(
    argument: HypothesisArgumentV2,
    evidence: list[EvidenceItem],
    bundle_evidence: list[EvidenceItem],
    errors: list[ValidationIssue],
) -> None:
    called = {trace.tool_name for trace in argument.tool_calls}
    missing_tools = sorted(_REQUIRED_BUNDLE_TOOLS - called)
    if argument.hypothesis_status == "supported" and missing_tools:
        errors.append(
            ValidationIssue(
                code="MISSING_REQUIRED_TOOL_CALL",
                path="$.tool_calls",
                message=f"supported hypothesis 缺少必查 Tool：{', '.join(missing_tools)}。",
            )
        )
    if (
        argument.hypothesis_status in {"supported", "weak"}
        and not argument.strongest_countercase
    ):
        errors.append(
            ValidationIssue(
                code="COUNTERCASE_REQUIRED",
                path="$.strongest_countercase",
                message="方向假设必须列出至少一项对己方不利的证据。",
            )
        )
    if (
        argument.hypothesis_status == "supported"
        and not argument.invalidation_conditions
    ):
        errors.append(
            ValidationIssue(
                code="INVALIDATION_REQUIRED",
                path="$.invalidation_conditions",
                message="supported hypothesis 必须提供结构化失效条件。",
            )
        )
    if argument.hypothesis_status != "supported":
        return

    families = {item.evidence_family_id for item in evidence if item.evidence_family_id}
    if len(families) < 2:
        errors.append(
            ValidationIssue(
                code="INSUFFICIENT_EVIDENCE_FAMILIES",
                path="$.coverage.evidence_family_ids",
                message="supported hypothesis 至少需要两个独立 evidence family。",
            )
        )
    if not families.issubset(set(argument.coverage.evidence_family_ids)):
        errors.append(
            ValidationIssue(
                code="COVERAGE_MISMATCH",
                path="$.coverage.evidence_family_ids",
                message="coverage 未列出所有实际引用的 evidence family。",
            )
        )
    by_id = {item.evidence_id: item for item in evidence}
    chain_evidence = [
        by_id[evidence_id]
        for chain in argument.causal_chains
        for evidence_id in chain.evidence_ids
        if evidence_id in by_id
    ]
    confirmation_evidence = [
        by_id[evidence_id]
        for confirmation in argument.market_confirmations
        for evidence_id in confirmation.evidence_ids
        if evidence_id in by_id
    ]
    if not any(item.domain == "macro" for item in chain_evidence) or not any(
        item.domain in {"market", "technical"} for item in confirmation_evidence
    ):
        errors.append(
            ValidationIssue(
                code="CROSS_DOMAIN_CONFIRMATION_REQUIRED",
                path="$.causal_chains",
                message="supported hypothesis 必须在 causal chain 中包含宏观机制，并单列市场/技术确认。",
            )
        )
    if not any(
        item.domain == "technical" and item.timeframe == "4H"
        for item in confirmation_evidence
    ):
        errors.append(
            ValidationIssue(
                code="MISSING_4H_CONFIRMATION",
                path="$.market_confirmations",
                message="缺少完整 4H 技术证据时不能标记 supported。",
            )
        )
    if any(
        item.name == "spot_quote" and item.quality_status == "abnormal"
        for item in bundle_evidence
    ):
        errors.append(
            ValidationIssue(
                code="ABNORMAL_EVIDENCE",
                path="$.causal_chains",
                message="引用异常报价或证据时不能标记 supported。",
            )
        )
    macro_evidence = [item for item in chain_evidence if item.domain == "macro"]
    if macro_evidence and not any(
        isinstance(item.value, dict) and item.value.get("forecast") is not None
        for item in macro_evidence
    ):
        errors.append(
            ValidationIssue(
                code="MACRO_FORECAST_UNAVAILABLE",
                path="$.causal_chains",
                message="引用的宏观机制均缺少 forecast 时不能标记 supported。",
            )
        )
    for chain in argument.causal_chains:
        text = f"{chain.observed_fact} {chain.inference}".lower()
        if "surprise" not in text and "超预期" not in text:
            continue
        referenced = [
            item for item in evidence if item.evidence_id in chain.evidence_ids
        ]
        if any(
            item.domain == "macro"
            and isinstance(item.value, dict)
            and item.value.get("forecast") is None
            for item in referenced
        ):
            errors.append(
                ValidationIssue(
                    code="MACRO_SURPRISE_UNAVAILABLE",
                    path="$.causal_chains",
                    message="forecast 缺失的宏观记录不能支持 surprise/超预期 claim。",
                )
            )


def _check_relative_state(
    state: RelativeStateV2,
    evidence: list[EvidenceItem],
    errors: list[ValidationIssue],
) -> None:
    called = {trace.tool_name for trace in state.tool_calls}
    missing_tools = sorted(_REQUIRED_BUNDLE_TOOLS - called)
    if state.analysis_status == "complete" and missing_tools:
        errors.append(
            ValidationIssue(
                code="MISSING_REQUIRED_TOOL_CALL",
                path="$.tool_calls",
                message=f"完整状态分析缺少必查 Tool：{', '.join(missing_tools)}。",
            )
        )
    has_4h = any(
        item.domain == "technical" and item.timeframe == "4H" for item in evidence
    )
    if state.technical_state != "indeterminate" and not has_4h:
        errors.append(
            ValidationIssue(
                code="TECHNICAL_STATE_WITHOUT_4H",
                path="$.technical_state",
                message="缺少完整 4H 技术证据时 technical_state 必须为 indeterminate。",
            )
        )
    has_macro = any(item.domain == "macro" for item in evidence)
    if state.relative_macro_state != "indeterminate" and not has_macro:
        errors.append(
            ValidationIssue(
                code="MACRO_STATE_WITHOUT_EVIDENCE",
                path="$.relative_macro_state",
                message="无宏观 evidence 时 relative_macro_state 必须为 indeterminate。",
            )
        )


def _check_risk_review(
    review: RiskReview,
    arguments: list[FrontAgentOutput],
    context: EvidenceContext,
    errors: list[ValidationIssue],
) -> None:
    claim_ids = {
        claim.claim_id for argument in arguments for claim in claims_view(argument)
    }
    referenced = set(review.approved_claim_ids)
    referenced.update(item.claim_id for item in review.rejected_claims)
    referenced.update(
        claim_id
        for group in review.duplicate_claim_groups
        for claim_id in group.claim_ids
    )
    _unknown_claim_errors(referenced - claim_ids, "$", errors)
    cap = _RISK_CAPS[context.risk_profile]
    if review.risk_limit.max_risk_per_trade_pct > cap:
        errors.append(
            ValidationIssue(
                code="RISK_LIMIT_EXCEEDED",
                path="$.risk_limit.max_risk_per_trade_pct",
                message=f"{context.risk_profile} 最大允许 {cap}%。",
            )
        )
    if any(_front_status(argument) != "complete" for argument in arguments) and not set(
        review.allowed_actions
    ).issubset({"wait", "hedge"}):
        errors.append(
            ValidationIssue(
                code="INSUFFICIENT_EVIDENCE_ACTION",
                path="$.allowed_actions",
                message="任一前置分析未达到 complete/supported 时只允许 wait 或 hedge。",
            )
        )


def _check_decision(
    decision: FinalDecision,
    arguments: list[FrontAgentOutput],
    review: RiskReview | None,
    context: EvidenceContext,
    errors: list[ValidationIssue],
) -> None:
    if review is None:
        raise ValueError("decision requires risk_review")
    claim_ids = {
        claim.claim_id for argument in arguments for claim in claims_view(argument)
    }
    _unknown_claim_errors(
        (set(decision.adopted_claim_ids) | set(decision.rejected_claim_ids))
        - claim_ids,
        "$",
        errors,
    )
    if decision.decision not in review.allowed_actions:
        errors.append(
            ValidationIssue(
                code="ACTION_NOT_ALLOWED",
                path="$.decision",
                message=f"RiskReview 未允许 {decision.decision}。",
            )
        )
    missing_conditions = set(review.required_invalidation_conditions) - set(
        decision.invalidation_conditions
    )
    if missing_conditions:
        errors.append(
            ValidationIssue(
                code="REQUIRED_INVALIDATION_MISSING",
                path="$.invalidation_conditions",
                message=f"缺少 Risk 强制失效条件：{sorted(missing_conditions)}。",
            )
        )
    expected_requested = (
        f"{context.requested_base_currency}/{context.requested_quote_currency}"
    )
    static_checks = {
        "$.canonical_symbol": (decision.canonical_symbol, context.canonical_symbol),
        "$.display_symbol": (decision.display_symbol, context.display_symbol),
        "$.requested_symbol": (decision.requested_symbol, expected_requested),
        "$.inverted": (decision.inverted, context.inverted),
        "$.horizon_days": (decision.horizon_days, context.horizon_days),
    }
    for path, (actual, expected) in static_checks.items():
        if actual != expected:
            errors.append(
                ValidationIssue(
                    code="CONTEXT_VALUE_MISMATCH",
                    path=path,
                    message=f"期望 {expected!r}，实际 {actual!r}。",
                )
            )
    if decision.data_as_of > context.as_of:
        errors.append(
            ValidationIssue(
                code="DATA_AS_OF_AFTER_CONTEXT",
                path="$.data_as_of",
                message="FinalDecision data_as_of 不得晚于 Evidence Context as_of。",
            )
        )


def _check_context(
    actual: str,
    context: EvidenceContext,
    errors: list[ValidationIssue],
    path: str,
) -> None:
    if actual != context.evidence_context_id:
        errors.append(
            ValidationIssue(
                code="CONTEXT_MISMATCH",
                path=path,
                message="输出不属于当前 Evidence Context。",
            )
        )


def _unknown_claim_errors(
    unknown: set[str], path: str, errors: list[ValidationIssue]
) -> None:
    for claim_id in sorted(unknown):
        errors.append(
            ValidationIssue(
                code="CLAIM_NOT_FOUND",
                path=path,
                message=f"上游不存在 Claim {claim_id!r}。",
            )
        )


def _schema_issues(exc: ValidationError) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for item in exc.errors(include_url=False):
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in item["loc"]
        )
        issues.append(
            ValidationIssue(
                code="SCHEMA_VALIDATION_ERROR",
                path=path,
                message=item["msg"],
            )
        )
    return issues


def _object(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a JSON object")
    return value


def _parse_front_output(value: dict[str, Any]) -> FrontAgentOutput:
    if value.get("schema_version") == "2.0":
        if value.get("agent_role") == "relative_macro_technical":
            return RelativeStateV2.model_validate(value)
        return HypothesisArgumentV2.model_validate(value)
    return AgentArgument.model_validate(value)


def _front_status(output: FrontAgentOutput) -> str:
    if isinstance(output, HypothesisArgumentV2):
        return (
            "insufficient_evidence"
            if output.hypothesis_status == "insufficient"
            else "partial" if output.hypothesis_status == "weak" else "complete"
        )
    return output.analysis_status
