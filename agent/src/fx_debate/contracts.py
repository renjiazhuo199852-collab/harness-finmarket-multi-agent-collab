"""Structured output contracts shared by the five FX Debate Agents."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(_Contract):
    claim_id: str
    statement: str
    evidence_ids: list[str] = Field(min_length=1)
    reasoning: str
    impact: Literal["positive", "negative", "mixed"]
    horizon_relevance: Literal["high", "medium", "low"]


class CounterEvidence(_Contract):
    evidence_ids: list[str] = Field(min_length=1)
    explanation: str


class AnalysisSections(_Contract):
    relative_macro_assessment: str
    technical_assessment: str
    cross_confirmation: str


class TradeCase(_Contract):
    action: Literal["long", "short", "wait"]
    entry_zone: tuple[float, float] | None
    stop_loss: float | None
    targets: list[float]

    @model_validator(mode="after")
    def validate_wait_shape(self) -> "TradeCase":
        if self.action == "wait" and (
            self.entry_zone is not None or self.stop_loss is not None or self.targets
        ):
            raise ValueError("wait requires null entry/stop and empty targets")
        if self.entry_zone is not None and self.entry_zone[0] > self.entry_zone[1]:
            raise ValueError("entry_zone lower bound must not exceed upper bound")
        return self


class ToolCallTrace(_Contract):
    tool_name: str
    query_id: str

    @model_validator(mode="before")
    @classmethod
    def accept_runtime_trace_shape(cls, value: Any) -> Any:
        """Accept the compact trace emitted by LLMs without weakening tool checks."""
        if not isinstance(value, dict):
            if isinstance(value, str):
                tool_name = value.split(":", 1)[0].strip()
                return {"tool_name": tool_name, "query_id": value}
            return value
        normalized = dict(value)
        tool_name = normalized.get("tool_name") or normalized.get("tool") or normalized.get("call")
        query_id = normalized.get("query_id") or normalized.get("call_id")
        if tool_name:
            normalized["tool_name"] = str(tool_name)
        normalized["query_id"] = str(query_id or tool_name or "unspecified")
        for key in ("status", "tool", "purpose", "operation", "call", "params", "result_evidence_ids"):
            normalized.pop(key, None)
        return normalized


class EvidenceStatement(_Contract):
    statement: str
    evidence_ids: list[str] = Field(min_length=1)


class CausalChain(_Contract):
    claim_id: str
    observed_fact: str
    inference: str
    transmission_mechanism: str
    expected_effect: Literal["up", "down"]
    effective_window: str
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def accept_compact_chain_shape(cls, value: Any) -> Any:
        """Map the older driver/transmission vocabulary to the V2 chain fields."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        # A deployed prompt version emitted typed ``steps`` instead of the
        # canonical V2 chain fields. Preserve its text and evidence IDs.
        steps = normalized.pop("steps", None)
        if isinstance(steps, list):
            step_text: dict[str, str] = {}
            step_evidence: list[str] = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_type = str(step.get("step_type") or step.get("type") or "").lower()
                description = str(step.get("description") or step.get("statement") or "").strip()
                if step_type and description:
                    step_text[step_type] = description
                step_evidence.extend(str(item) for item in (step.get("evidence_ids") or []) if item)
            normalized.setdefault("observed_fact", step_text.get("observed_fact", ""))
            normalized.setdefault("inference", step_text.get("inference", ""))
            normalized.setdefault("transmission_mechanism", step_text.get("transmission", ""))
            normalized.setdefault("effective_window", step_text.get("window", ""))
            normalized.setdefault("expected_effect", step_text.get("expected_effect", normalized.get("direction", "down")))
            if step_evidence:
                normalized.setdefault("evidence_ids", list(dict.fromkeys(step_evidence)))
        drivers = normalized.pop("driver", [])
        observed_facts = normalized.pop("observed_facts", [])
        if observed_facts and not drivers:
            drivers = observed_facts
        if isinstance(drivers, dict):
            drivers = [drivers]
        if isinstance(drivers, str):
            normalized.setdefault("observed_fact", drivers)
            normalized.setdefault(
                "evidence_ids",
                re.findall(r"\[(fxe-[A-Za-z0-9_-]+)\]", drivers),
            )
        elif isinstance(drivers, list):
            statements = [
                str(
                    item.get("statement") or item.get("observation") or item.get("fact")
                )
                for item in drivers
                if isinstance(item, dict)
                and (
                    item.get("statement") or item.get("observation") or item.get("fact")
                )
            ]
            evidence_ids = [
                evidence_id
                for item in drivers
                if isinstance(item, dict)
                for evidence_id in item.get("evidence_ids", [])
            ]
            if statements:
                normalized["observed_fact"] = "；".join(statements)
            if evidence_ids:
                normalized.setdefault("evidence_ids", evidence_ids)
            if not normalized.get("evidence_ids"):
                normalized["evidence_ids"] = re.findall(
                    r"\[(fxe-[A-Za-z0-9_-]+)\]",
                    str(normalized.get("observed_fact") or ""),
                )
        normalized["claim_id"] = normalized.get("claim_id") or normalized.get(
            "chain_id", normalized.get("id", "chain-1")
        )
        normalized["transmission_mechanism"] = normalized.get(
            "transmission_mechanism", normalized.pop("transmission", "")
        )
        normalized["effective_window"] = normalized.get(
            "effective_window", normalized.pop("window", "")
        )
        effect = normalized.get("expected_effect")
        if effect not in {"up", "down"}:
            normalized["expected_effect"] = (
                "up" if "up" in str(effect).lower() else "down"
            )
        for key in (
            "chain_id",
            "id",
            "label",
            "direction",
            "strength",
            "steps",
            "driver",
            "observed_facts",
            "driver_type",
            "limitations",
            # Some models attach the argument-level audit trace to an
            # individual chain when serializing nested JSON.  Tool traces are
            # intentionally owned by HypothesisArgumentV2.tool_calls; they do
            # not change the causal claim and must not block Risk re-validation
            # of an otherwise valid upstream argument.
            "tool_calls",
        ):
            normalized.pop(key, None)
        return normalized


class StructuredInvalidation(_Contract):
    metric: str
    operator: Literal["<", "<=", ">", ">=", "==", "changes_to"]
    threshold: float | str
    valid_until: datetime | None = None
    evidence_family_id: str

    @model_validator(mode="before")
    @classmethod
    def accept_text_condition(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {
                "metric": "qualitative_condition",
                "operator": "changes_to",
                "threshold": value,
                "evidence_family_id": "unscoped",
            }
        return value


def _evidence_ids_from(value: Any) -> list[str]:
    """Extract real evidence IDs from one compact model fragment."""
    if not isinstance(value, dict):
        return []
    return [str(item) for item in (value.get("evidence_ids") or []) if item]


def _statement_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in (
        "statement",
        "description",
        "observation",
        "implication",
        "inference",
        "condition",
        "rationale",
    ):
        text = value.get(key)
        if text:
            return str(text).strip()
    return ""


def _statement_list(
    value: Any, fallback_evidence_ids: list[str]
) -> list[dict[str, Any]]:
    """Normalize a statement, singleton object, or legacy list."""
    items = value if isinstance(value, list) else [value] if value else []
    result: list[dict[str, Any]] = []
    for item in items:
        text = _statement_text(item)
        if not text:
            continue
        evidence_ids = _evidence_ids_from(item)
        if not evidence_ids:
            evidence_ids = list(fallback_evidence_ids)
        if not evidence_ids:
            continue
        result.append({"statement": text, "evidence_ids": evidence_ids})
    return result


def _unique_identifier(
    value: Any,
    *,
    seen: set[str],
    fallback: str,
) -> str:
    """Return a stable, non-empty identifier without trusting model uniqueness.

    LLMs frequently omit IDs or reuse a plausible label while expanding a list.
    IDs are only local references inside one output, so a deterministic suffix is
    safer than rejecting an otherwise useful analysis during a retry.
    """
    base = str(value or fallback).strip() or fallback
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = f"{base}-{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


class EvidenceCoverage(_Contract):
    domains: list[Literal["market", "technical", "macro", "news"]]
    evidence_family_ids: list[str]
    limitations: list[str]


class HypothesisArgumentV2(_Contract):
    """Directional, falsifiable output shared symmetrically by Bull and Bear."""

    schema_version: Literal["2.0"]
    evidence_context_id: str
    agent_role: Literal["pair_bull", "pair_bear"]
    hypothesis_direction: Literal["up", "down"]
    hypothesis_status: Literal["supported", "weak", "insufficient"]
    summary: str
    causal_chains: list[CausalChain] = Field(max_length=3)
    catalysts: list[EvidenceStatement]
    market_confirmations: list[EvidenceStatement]
    strongest_countercase: list[EvidenceStatement]
    invalidation_conditions: list[StructuredInvalidation]
    coverage: EvidenceCoverage
    strength: Literal["low", "medium", "high"]
    missing_data: list[str]
    tool_calls: list[ToolCallTrace]

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_v2_aliases(cls, value: Any) -> Any:
        """Normalize common pre-V2 aliases before strict nested validation."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        role = normalized.get("agent_role", "pair_bull")
        direction = "up" if role == "pair_bull" else "down"
        normalized["hypothesis_direction"] = normalized.get(
            "hypothesis_direction", direction
        )
        strength_aliases = {"weak": "low", "moderate": "medium", "strong": "high"}
        raw_strength_value = normalized.get("strength", "low")
        if isinstance(raw_strength_value, dict):
            raw_strength_value = raw_strength_value.get("rating", "low")
        raw_strength = str(raw_strength_value).lower()
        if "strength" not in normalized and normalized.get("rating"):
            raw_strength = str(normalized["rating"]).lower()
        normalized.pop("rating", None)
        normalized["strength"] = strength_aliases.get(raw_strength, raw_strength)
        if (
            normalized.get("hypothesis_status") != "supported"
            and normalized.get("strength") == "high"
        ):
            normalized["strength"] = "medium"

        status_aliases = {
            "rejected": "weak",
            "unsupported": "weak",
            "not_supported": "weak",
            "not_supported_yet": "weak",
            "pending": "insufficient",
            "insufficient_evidence": "insufficient",
        }
        raw_status = str(normalized.get("hypothesis_status") or "").lower().strip()
        if raw_status in status_aliases:
            normalized["hypothesis_status"] = status_aliases[raw_status]
        elif raw_status not in {"supported", "weak", "insufficient"}:
            normalized["hypothesis_status"] = (
                "weak" if normalized.get("causal_chains") else "insufficient"
            )

        summary = normalized.get("summary")
        if isinstance(summary, dict):
            summary_parts = [
                _statement_text(summary.get(key))
                for key in ("statement", "thesis", "rationale", "largest_limitation")
            ]
            normalized["summary"] = (
                "；".join(part for part in summary_parts if part)
                or "模型未提供可读摘要。"
            )
        elif summary is None:
            normalized["summary"] = "模型未提供可读摘要。"
        elif not isinstance(summary, str):
            normalized["summary"] = str(summary)

        chains = []
        dropped_chain = False
        seen_claim_ids: set[str] = set()
        for index, item in enumerate(
            normalized.get("causal_chains", []) or [], start=1
        ):
            if isinstance(item, dict):
                chain = dict(item)
                chain["claim_id"] = _unique_identifier(
                    chain.get("claim_id") or chain.get("chain_id") or chain.get("id"),
                    seen=seen_claim_ids,
                    fallback=f"{role}-chain-{index}",
                )
                chain.setdefault("expected_effect", direction)
                if chain.get("expected_effect") not in {"up", "down"} or (
                    normalized.get("hypothesis_status") != "supported"
                    and chain.get("expected_effect") != direction
                ):
                    chain["expected_effect"] = direction
                chains.append(chain)
            else:
                # Do not let a prose-only chain crash the whole DAG. It has
                # no auditable evidence, so downgrade the hypothesis below.
                dropped_chain = True
        normalized["causal_chains"] = chains
        if dropped_chain and normalized.get("hypothesis_status") == "supported":
            normalized["hypothesis_status"] = "weak"

        fallback_evidence_ids: list[str] = []
        for key in (
            "causal_chains",
            "catalysts",
            "market_confirmations",
            "strongest_countercase",
        ):
            values = normalized.get(key, [])
            values = values if isinstance(values, list) else [values]
            for item in values:
                fallback_evidence_ids.extend(_evidence_ids_from(item))
                if isinstance(item, dict):
                    fallback_evidence_ids.extend(
                        re.findall(
                            r"\[(fxe-[A-Za-z0-9_-]+)\]",
                            str(item.get("observed_fact") or ""),
                        )
                    )
                    for observed in item.get("observed_facts", []) or []:
                        fallback_evidence_ids.extend(_evidence_ids_from(observed))
        fallback_evidence_ids = list(dict.fromkeys(fallback_evidence_ids))
        normalized["catalysts"] = _statement_list(
            normalized.get("catalysts"), fallback_evidence_ids
        )
        normalized["market_confirmations"] = _statement_list(
            normalized.get("market_confirmations"), fallback_evidence_ids
        )
        normalized["strongest_countercase"] = _statement_list(
            normalized.get("strongest_countercase"), fallback_evidence_ids
        )

        invalidations = normalized.get("invalidation_conditions", []) or []
        invalidations = (
            invalidations if isinstance(invalidations, list) else [invalidations]
        )
        normalized_invalidations: list[dict[str, Any] | str] = []
        for item in invalidations:
            if isinstance(item, str):
                normalized_invalidations.append(item)
                continue
            if not isinstance(item, dict):
                continue
            raw_condition = dict(item)
            condition = {
                "metric": raw_condition.get("metric") or "qualitative_condition",
                "operator": (
                    raw_condition.get("operator")
                    if raw_condition.get("operator")
                    in {"<", "<=", ">", ">=", "==", "changes_to"}
                    else "changes_to"
                ),
                "threshold": (
                    raw_condition.get("threshold")
                    or raw_condition.get("condition")
                    or raw_condition.get("description")
                    or raw_condition.get("rationale")
                    or "condition changes"
                ),
                "valid_until": raw_condition.get("valid_until"),
                "evidence_family_id": raw_condition.get("evidence_family_id") or "unscoped",
            }
            normalized_invalidations.append(condition)
        normalized["invalidation_conditions"] = normalized_invalidations
        missing_data = normalized.get("missing_data", []) or []
        if not isinstance(missing_data, list):
            missing_data = [missing_data]
        normalized["missing_data"] = [
            _statement_text(item) or str(item)
            for item in missing_data
            if _statement_text(item) or isinstance(item, (str, int, float))
        ]
        # Some model runs echo the frozen context timestamp at the top level.
        # It is metadata already carried by the Tool argument, not part of V2.
        normalized.pop("as_of", None)

        coverage = normalized.get("coverage")
        if isinstance(coverage, dict) and "domains" not in coverage:
            domains = []
            families = []
            limitations = []
            for domain in ("market", "technical", "macro", "news"):
                item = coverage.get(domain)
                if not isinstance(item, dict):
                    continue
                domains.append(domain)
                families.extend(item.get("evidence_ids", []))
                limitations.extend(item.get("limitations", []))
            normalized["coverage"] = {
                "domains": domains,
                "evidence_family_ids": list(dict.fromkeys(families)),
                "limitations": limitations,
            }

        if isinstance(normalized.get("coverage"), dict):
            domains = normalized["coverage"].get("domains")
            if isinstance(domains, dict):
                domains = list(domains)
            if isinstance(domains, list):
                normalized["coverage"]["domains"] = [
                    str(domain).split(":", 1)[0].strip()
                    for domain in domains
                    if str(domain).split(":", 1)[0].strip()
                    in {"market", "technical", "macro", "news"}
                ]
            normalized["coverage"].pop("as_of", None)
        tool_calls = normalized.get("tool_calls", []) or []
        if not isinstance(tool_calls, list):
            tool_calls = [tool_calls]
        normalized["tool_calls"] = tool_calls
        return normalized

    @model_validator(mode="after")
    def validate_hypothesis_shape(self) -> "HypothesisArgumentV2":
        expected = "up" if self.agent_role == "pair_bull" else "down"
        if self.hypothesis_direction != expected:
            raise ValueError(
                f"{self.agent_role} requires hypothesis_direction={expected}"
            )
        claim_ids = [chain.claim_id for chain in self.causal_chains]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique within one hypothesis")
        if any(chain.expected_effect != expected for chain in self.causal_chains):
            raise ValueError(
                "causal-chain expected_effect must match hypothesis direction"
            )
        if self.hypothesis_status == "supported" and self.strength == "low":
            raise ValueError("supported hypothesis cannot have low strength")
        if self.hypothesis_status != "supported" and self.strength == "high":
            raise ValueError("high strength requires supported hypothesis")
        if self.hypothesis_status == "insufficient" and self.causal_chains:
            raise ValueError("insufficient hypothesis must not contain causal chains")
        return self


class StateFinding(_Contract):
    claim_id: str
    dimension: Literal["macro", "technical", "cross_confirmation", "event"]
    statement: str
    evidence_ids: list[str] = Field(min_length=1)


class RelativeStateV2(_Contract):
    """Neutral relative-macro and technical state; never a trade recommendation."""

    schema_version: Literal["2.0"]
    evidence_context_id: str
    agent_role: Literal["relative_macro_technical"]
    analysis_status: Literal["complete", "partial", "insufficient_evidence"]
    relative_macro_state: Literal[
        "base_supported", "quote_supported", "balanced", "indeterminate"
    ]
    technical_state: Literal[
        "bullish", "bearish", "range", "transition", "indeterminate"
    ]
    cross_confirmation: Literal[
        "aligned_up",
        "aligned_down",
        "macro_leads",
        "price_leads",
        "diverging",
        "indeterminate",
    ]
    findings: list[StateFinding] = Field(max_length=8)
    event_state: Literal["normal", "pre_event", "post_event", "unknown"]
    reliability: Literal["low", "medium", "high"]
    summary: str
    missing_data: list[str]
    tool_calls: list[ToolCallTrace]

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_state_aliases(cls, value: Any) -> Any:
        """Normalize observations and unavailable-state markers into RelativeStateV2."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if not normalized.get("evidence_context_id"):
            # The Tool fills this from its required context parameter before validation.
            normalized["evidence_context_id"] = normalized.get("context_id", "")
        event_state = normalized.get("event_state")
        if not isinstance(event_state, str) or event_state not in {
            "normal",
            "pre_event",
            "post_event",
            "unknown",
        }:
            normalized["event_state"] = "unknown"
        findings = []
        seen_claim_ids: set[str] = set()
        for index, item in enumerate(normalized.get("findings", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            finding = dict(item)
            finding["claim_id"] = _unique_identifier(
                finding.get("claim_id"),
                seen=seen_claim_ids,
                fallback=f"finding-{index}",
            )
            dimension = str(finding.get("dimension", "")).lower().replace(" ", "_")
            dimension_aliases = {
                "relative_macro": "macro",
                "macro_technical": "cross_confirmation",
                "cross": "cross_confirmation",
            }
            dimension = dimension_aliases.get(dimension, dimension)
            if dimension in {
                "rates",
                "growth",
                "labor",
                "inflation",
                "macro_rates",
                "macro_surprise",
            }:
                dimension = "macro"
            # Some model runs preserve a more descriptive dimension name such as
            # ``macro_rates`` or ``technical_regime``.  Keep the V2 contract
            # semantic while accepting those descriptive labels.
            if dimension.startswith("macro"):
                dimension = "macro"
            elif dimension.startswith("technical"):
                dimension = "technical"
            elif "cross" in dimension:
                dimension = "cross_confirmation"
            elif dimension.startswith("event"):
                dimension = "event"
            finding["dimension"] = dimension
            if "statement" not in finding:
                parts = [
                    finding.pop(key, "")
                    for key in ("observation", "implication", "horizon_relevance")
                ]
                finding["statement"] = "；".join(str(part) for part in parts if part)
            evidence_ids = [
                evidence_id
                for evidence_id in (finding.get("evidence_ids") or [])
                if not _is_unavailable_evidence_id(evidence_id)
            ]
            # A limitation finding has no positive evidence. Keep it in missing_data,
            # but do not turn it into a downstream claim.
            if not evidence_ids:
                continue
            finding["evidence_ids"] = evidence_ids
            for key in (
                "observation",
                "implication",
                "horizon_relevance",
                "interpretation",
                "state",
            ):
                finding.pop(key, None)
            findings.append(finding)
        normalized["findings"] = findings
        missing_data = normalized.get("missing_data", []) or []
        if not isinstance(missing_data, list):
            missing_data = [missing_data]
        normalized["missing_data"] = [
            _statement_text(item) or str(item)
            for item in missing_data
            if _statement_text(item) or isinstance(item, (str, int, float))
        ]
        tool_calls = normalized.get("tool_calls", []) or []
        if not isinstance(tool_calls, list):
            tool_calls = [tool_calls]
        normalized["tool_calls"] = tool_calls
        return normalized

    @model_validator(mode="after")
    def validate_state_shape(self) -> "RelativeStateV2":
        claim_ids = [finding.claim_id for finding in self.findings]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique within one state output")
        if (
            self.analysis_status == "insufficient_evidence"
            and self.reliability != "low"
        ):
            raise ValueError("insufficient evidence requires low reliability")
        return self


def _is_unavailable_evidence_id(value: Any) -> bool:
    """Recognize model placeholders for unavailable derived state.

    The evidence factory deliberately does not register synthetic IDs for an
    indeterminate 4H/event state.  Some model responses nevertheless invent
    labels such as ``fxe-tech-4h-missing``.  They are limitations, not claims,
    so discard them before evidence lookup while keeping real IDs strict.
    """
    normalized = str(value).strip().lower()
    if not normalized.startswith("fxe-"):
        return False
    return any(
        marker in normalized
        for marker in ("missing", "incomplete", "indeterminate", "unavailable")
    )


class AgentArgument(_Contract):
    schema_version: Literal["1.0"]
    evidence_context_id: str
    agent_role: Literal["pair_bull", "pair_bear", "relative_macro_technical"]
    analysis_status: Literal["complete", "partial", "insufficient_evidence"]
    stance: Literal["BULL", "BEAR", "NEUTRAL"]
    summary: str
    claims: list[Claim] = Field(max_length=5)
    counter_evidence: list[CounterEvidence]
    analysis_sections: AnalysisSections | None
    trade_case: TradeCase
    invalidation_conditions: list[str]
    confidence: float = Field(ge=0, le=1)
    missing_data: list[str]
    tool_calls: list[ToolCallTrace]

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_claim_ids(cls, value: Any) -> Any:
        """Keep V1 replay outputs usable when a model omits/reuses claim IDs."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        seen: set[str] = set()
        claims = []
        for index, item in enumerate(normalized.get("claims", []) or [], start=1):
            if not isinstance(item, dict):
                claims.append(item)
                continue
            claim = dict(item)
            claim["claim_id"] = _unique_identifier(
                claim.get("claim_id"),
                seen=seen,
                fallback=f"claim-{index}",
            )
            claims.append(claim)
        normalized["claims"] = claims
        return normalized

    @model_validator(mode="after")
    def validate_role_specific_shape(self) -> "AgentArgument":
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique within one argument")
        if self.analysis_status == "complete" and len(self.claims) < 2:
            raise ValueError("complete analysis requires 2 to 5 claims")
        if (
            self.agent_role == "relative_macro_technical"
            and self.analysis_sections is None
        ):
            raise ValueError("Macro & Technical requires analysis_sections")
        if (
            self.agent_role != "relative_macro_technical"
            and self.analysis_sections is not None
        ):
            raise ValueError("Pair Bull/Bear analysis_sections must be null")
        if (
            self.analysis_status == "insufficient_evidence"
            and self.trade_case.action != "wait"
        ):
            raise ValueError("insufficient evidence requires a wait trade case")
        return self


class RejectedClaim(_Contract):
    claim_id: str
    reason_code: str
    reason: str


class DuplicateClaimGroup(_Contract):
    claim_ids: list[str] = Field(min_length=2)
    shared_evidence_ids: list[str] = Field(min_length=1)


class EvidenceConflict(_Contract):
    evidence_ids: list[str] = Field(min_length=2)
    description: str


class RiskLimit(_Contract):
    max_risk_per_trade_pct: float = Field(gt=0, le=100)
    basis: str


class RiskReview(_Contract):
    evidence_context_id: str
    approved_claim_ids: list[str]
    rejected_claims: list[RejectedClaim]
    duplicate_claim_groups: list[DuplicateClaimGroup]
    evidence_conflicts: list[EvidenceConflict]
    risk_level: Literal["low", "medium", "high", "critical"]
    allowed_actions: list[Literal["long", "short", "wait", "hedge"]] = Field(
        min_length=1
    )
    risk_limit: RiskLimit
    required_invalidation_conditions: list[str]
    missing_data: list[str]
    risk_summary: str


class ScenarioProbabilities(_Contract):
    bull: float = Field(ge=0, le=1)
    base: float = Field(ge=0, le=1)
    bear: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_unit_sum(self) -> "ScenarioProbabilities":
        if abs(self.bull + self.base + self.bear - 1) > 0.01:
            raise ValueError("scenario probabilities must sum to 1")
        return self


class TradePlan(_Contract):
    entry_zone: tuple[float, float] | None
    stop_loss: float | None
    targets: list[float]

    @model_validator(mode="after")
    def validate_zone(self) -> "TradePlan":
        if self.entry_zone is not None and self.entry_zone[0] > self.entry_zone[1]:
            raise ValueError("entry_zone lower bound must not exceed upper bound")
        return self


class FinalDecision(_Contract):
    evidence_context_id: str
    canonical_symbol: str
    display_symbol: str
    requested_symbol: str
    inverted: bool
    direction_semantics: str
    decision: Literal["long", "short", "wait", "hedge"]
    confidence: float = Field(ge=0, le=1)
    horizon_days: int = Field(ge=1, le=90)
    scenario_probabilities: ScenarioProbabilities
    thesis: str
    adopted_claim_ids: list[str]
    rejected_claim_ids: list[str]
    key_evidence_ids: list[str]
    trade_plan: TradePlan
    risk_assessment: str
    invalidation_conditions: list[str]
    missing_data: list[str]
    data_as_of: datetime
    next_review_trigger: str

    @field_validator("data_as_of")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("data_as_of must include timezone")
        return value

    @model_validator(mode="after")
    def validate_wait_shape(self) -> "FinalDecision":
        if self.decision == "wait" and (
            self.trade_plan.entry_zone is not None
            or self.trade_plan.stop_loss is not None
            or self.trade_plan.targets
        ):
            raise ValueError("wait requires null entry/stop and empty targets")
        return self


class ValidationIssue(_Contract):
    code: str
    path: str
    message: str


class ValidationResult(_Contract):
    valid: bool
    mode: Literal["argument", "hypothesis", "relative_state", "risk_review", "decision"]
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    checked_evidence_ids: list[str]


FrontAgentOutput = AgentArgument | HypothesisArgumentV2 | RelativeStateV2


def claims_view(output: FrontAgentOutput) -> list[Claim]:
    """Project V1/V2 front-agent outputs onto the downstream claim interface."""
    if isinstance(output, AgentArgument):
        return output.claims
    if isinstance(output, HypothesisArgumentV2):
        impact = "positive" if output.hypothesis_direction == "up" else "negative"
        return [
            Claim(
                claim_id=chain.claim_id,
                statement=chain.inference,
                evidence_ids=chain.evidence_ids,
                reasoning=chain.transmission_mechanism,
                impact=impact,
                horizon_relevance="high",
            )
            for chain in output.causal_chains
        ]
    return [
        Claim(
            claim_id=finding.claim_id,
            statement=finding.statement,
            evidence_ids=finding.evidence_ids,
            reasoning=f"Relative state finding: {finding.dimension}",
            impact="mixed",
            horizon_relevance="high",
        )
        for finding in output.findings
    ]
