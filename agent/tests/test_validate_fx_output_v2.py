"""Deterministic validation gates for FX front-agent V2 outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.fx_debate.context import build_evidence_context
from src.fx_debate.contracts import HypothesisArgumentV2, RelativeStateV2
from src.fx_debate.models import EvidenceItem, ResolvedFxDebateRequest, RunOptions
from src.fx_debate.store import FxEvidenceStore
from src.tools.validate_fx_output_tool import ValidateFxOutputTool


def _context():
    return build_evidence_context(
        ResolvedFxDebateRequest(
            status="resolved",
            asset_class="fx",
            instrument_type="spot",
            pair_class="major",
            canonical_symbol="EURUSD",
            display_symbol="EUR/USD",
            base_currency="EUR",
            quote_currency="USD",
            requested_base_currency="EUR",
            requested_quote_currency="USD",
            inverted=False,
            horizon="2 weeks",
            timeframe="4H/1D",
        ),
        RunOptions(as_of=datetime(2026, 8, 2, 12, tzinfo=timezone.utc)),
    )


def _validator(tmp_path):
    context = _context()
    store = FxEvidenceStore(tmp_path, context.evidence_context_id)
    store.register(
        [
            EvidenceItem(
                evidence_id="macro-1",
                evidence_context_id=context.evidence_context_id,
                evidence_family_id="macro-family",
                domain="macro",
                name="EU_PMI",
                value={"actual": 51, "forecast": None},
                unit="index",
                observation_time=context.as_of,
                available_time=context.as_of,
                source="TEST",
                source_table="macro_observations",
                quality_status="partial",
            ),
            EvidenceItem(
                evidence_id="technical-1d",
                evidence_context_id=context.evidence_context_id,
                evidence_family_id="technical-1d-family",
                domain="technical",
                name="ema_20",
                timeframe="1D",
                value=1.1,
                unit="price",
                observation_time=context.as_of,
                available_time=context.as_of,
                source="TEST",
                source_table="market_bars",
                quality_status="fresh",
            ),
            EvidenceItem(
                evidence_id="quote-abnormal",
                evidence_context_id=context.evidence_context_id,
                evidence_family_id="quote-family",
                domain="market",
                name="spot_quote",
                value={"bid": 1.2, "mid": 1.15, "ask": 1.1},
                unit="USD_per_EUR",
                observation_time=context.as_of,
                available_time=context.as_of,
                source="TEST",
                source_table="latest_prices",
                quality_status="abnormal",
            ),
        ]
    )
    return context, ValidateFxOutputTool(context=context, store=store)


def _supported_bull(context):
    return {
        "schema_version": "2.0",
        "evidence_context_id": context.evidence_context_id,
        "agent_role": "pair_bull",
        "hypothesis_direction": "up",
        "hypothesis_status": "supported",
        "summary": "测试上涨假设。",
        "causal_chains": [
            {
                "claim_id": "bull-c1",
                "observed_fact": "PMI surprise 为正。",
                "inference": "欧元相对预期改善。",
                "transmission_mechanism": "相对增长预期影响汇率。",
                "expected_effect": "up",
                "effective_window": "未来两周",
                "evidence_ids": ["macro-1", "technical-1d"],
            }
        ],
        "catalysts": [],
        "market_confirmations": [
            {"statement": "日线确认。", "evidence_ids": ["technical-1d"]}
        ],
        "strongest_countercase": [
            {"statement": "宏观预测缺失。", "evidence_ids": ["macro-1"]}
        ],
        "invalidation_conditions": [
            {
                "metric": "ema_20",
                "operator": "<",
                "threshold": 1.0,
                "valid_until": context.as_of.isoformat(),
                "evidence_family_id": "technical-1d-family",
            }
        ],
        "coverage": {
            "domains": ["macro", "technical"],
            "evidence_family_ids": ["macro-family", "technical-1d-family"],
            "limitations": [],
        },
        "strength": "high",
        "missing_data": [],
        "tool_calls": [
            {"tool_name": name, "query_id": name}
            for name in (
                "get_fx_evidence_manifest",
                "get_fx_relative_macro_scorecard",
                "get_fx_technical_regime",
            )
        ],
    }


def test_supported_hypothesis_requires_4h_and_real_macro_forecast(tmp_path) -> None:
    context, validator = _validator(tmp_path)

    result = json.loads(
        validator.execute(
            mode="hypothesis",
            evidence_context_id=context.evidence_context_id,
            output=_supported_bull(context),
        )
    )

    assert result["valid"] is False
    codes = {item["code"] for item in result["errors"]}
    assert "MISSING_4H_CONFIRMATION" in codes
    assert "MACRO_FORECAST_UNAVAILABLE" in codes
    assert "MACRO_SURPRISE_UNAVAILABLE" in codes
    assert "ABNORMAL_EVIDENCE" in codes


def test_hypothesis_role_direction_is_enforced_by_contract(tmp_path) -> None:
    context, validator = _validator(tmp_path)
    output = _supported_bull(context)
    output["hypothesis_direction"] = "down"

    result = json.loads(
        validator.execute(
            mode="hypothesis",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is False
    assert any(item["code"] == "SCHEMA_VALIDATION_ERROR" for item in result["errors"])


def test_relative_state_cannot_claim_direction_without_4h(tmp_path) -> None:
    context, validator = _validator(tmp_path)
    output = {
        "schema_version": "2.0",
        "evidence_context_id": context.evidence_context_id,
        "agent_role": "relative_macro_technical",
        "analysis_status": "partial",
        "relative_macro_state": "indeterminate",
        "technical_state": "bullish",
        "cross_confirmation": "price_leads",
        "findings": [
            {
                "claim_id": "mt-c1",
                "dimension": "technical",
                "statement": "日线状态向上。",
                "evidence_ids": ["technical-1d"],
            }
        ],
        "event_state": "unknown",
        "reliability": "low",
        "summary": "缺少4H。",
        "missing_data": ["missing 4H"],
        "tool_calls": [],
    }

    result = json.loads(
        validator.execute(
            mode="relative_state",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is False
    assert any(
        item["code"] == "TECHNICAL_STATE_WITHOUT_4H" for item in result["errors"]
    )


def test_relative_state_accepts_runtime_aliases_and_unavailable_findings(
    tmp_path,
) -> None:
    context, validator = _validator(tmp_path)
    output = {
        # This mirrors the compact shape emitted by the production model.
        "schema_version": "2.0",
        "agent_role": "relative_macro_technical",
        "analysis_status": "partial",
        "relative_macro_state": "indeterminate",
        "technical_state": "indeterminate",
        "cross_confirmation": "indeterminate",
        "findings": [
            {
                "observation": "欧元区利率记录可用。",
                "implication": "相对利率背景可见。",
                "horizon_relevance": "仅作慢频背景。",
                "dimension": "macro_rates",
                "evidence_ids": ["macro-1"],
            },
            {
                "observation": "4H K 线不足。",
                "implication": "技术状态不可判定。",
                "horizon_relevance": "不能提供价格确认。",
                "dimension": "technical",
                "evidence_ids": ["fxe-tech-4h-missing"],
            },
        ],
        "event_state": "indeterminate",
        "reliability": "low",
        "summary": "数据不足。",
        "missing_data": ["4H bars"],
        "tool_calls": [
            {"tool_name": "get_fx_evidence_manifest", "status": "success"},
            {"tool_name": "get_fx_relative_macro_scorecard", "status": "success"},
        ],
    }

    result = json.loads(
        validator.execute(
            mode="relative_state",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is True
    assert result["checked_evidence_ids"] == ["macro-1"]


def test_hypothesis_accepts_legacy_chain_aliases_with_context_in_tool_call(
    tmp_path,
) -> None:
    context, validator = _validator(tmp_path)
    output = {
        "schema_version": "2.0",
        "agent_role": "pair_bear",
        "hypothesis_status": "weak",
        "summary": "弱支持。",
        "causal_chains": [
            {
                "chain_id": "chain-1",
                "driver": [
                    {"statement": "利率背景偏向美元。", "evidence_ids": ["macro-1"]}
                ],
                "inference": "EUR/USD 可能承压。",
                "transmission": "相对回报影响资金配置。",
                "expected_effect": "下行",
                "window": "未来两周",
            }
        ],
        "catalysts": [
            {
                "type": "macro_or_policy",
                "description": "政策预期变化。",
                "evidence_ids": ["macro-1"],
            }
        ],
        "market_confirmations": [],
        "strongest_countercase": [
            {"statement": "技术确认不足。", "evidence_ids": ["macro-1"]}
        ],
        "invalidation_conditions": ["若价格结构转强则失效"],
        "coverage": {"macro": {"evidence_ids": ["macro-1"], "limitations": []}},
        "strength": "high",
        "missing_data": ["4H bars"],
        "tool_calls": [
            "get_fx_evidence_manifest:fxctx",
            "get_fx_technical_regime:fxctx",
        ],
    }

    result = json.loads(
        validator.execute(
            mode="hypothesis",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is True


def test_hypothesis_accepts_observed_run_shape_without_lowering_gates(tmp_path) -> None:
    """Replay the shape emitted by the failed UI run after compatibility mapping."""
    context, validator = _validator(tmp_path)
    output = {
        "schema_version": "2.0",
        "agent_role": "pair_bull",
        "hypothesis_status": "weak",
        "summary": "弱上涨假设，缺少价格确认。",
        "causal_chains": [
            {
                "chain_id": "rate-differential-counterpressure",
                "observed_facts": [
                    {
                        "statement": "美国利率高于欧元区。",
                        "evidence_ids": ["macro-1"],
                    }
                ],
                "inference": "利差可能压制 EUR/USD。",
                "transmission": "相对利率影响资金配置。",
                "expected_effect": "下行",
                "window": "未来两周",
            }
        ],
        "catalysts": [{"description": "新闻仅作背景。", "evidence_ids": ["macro-1"]}],
        "market_confirmations": [],
        "strongest_countercase": "美国利率优势持续。",
        "invalidation_conditions": [
            {
                "metric": "technical_regime",
                "operator": "equals",
                "threshold": "bullish",
                "description": "价格重新转强后失效。",
                "evidence_ids": ["technical-1d"],
            }
        ],
        "coverage": {
            "domains": {"market": "missing", "macro": "partial"},
            "evidence_family_ids": ["macro-family"],
            "limitations": ["4H 不足"],
        },
        "strength": {"rating": "weak", "rationale": "数据不完整"},
        "missing_data": ["4H bars"],
        "tool_calls": [
            {"tool": "get_fx_evidence_manifest", "status": "complete"},
            {"tool": "get_fx_technical_regime", "status": "complete"},
        ],
    }

    result = json.loads(
        validator.execute(
            mode="hypothesis",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is True
    assert result["checked_evidence_ids"] == ["macro-1"]


def test_relative_state_accepts_scorecard_dimensions_and_event_object(tmp_path) -> None:
    context, validator = _validator(tmp_path)
    output = {
        "schema_version": "2.0",
        "agent_role": "relative_macro_technical",
        "analysis_status": "partial",
        "relative_macro_state": "quote_supported",
        "technical_state": "indeterminate",
        "cross_confirmation": "indeterminate",
        "findings": [
            {
                "dimension": "rates",
                "observation": "美国利率高于欧元区。",
                "interpretation": "美元相对利率背景受支持。",
                "horizon_relevance": "仅作背景。",
                "state": "quote_supported",
                "evidence_ids": ["macro-1"],
            },
            {
                "dimension": "technical_1D",
                "observation": "日线不足。",
                "interpretation": "技术不可判定。",
                "horizon_relevance": "无价格确认。",
                "state": "indeterminate",
                "evidence_ids": [],
            },
        ],
        "event_state": {
            "state": "indeterminate",
            "summary": "事件日历未接入。",
            "evidence_ids": [],
        },
        "reliability": "low",
        "summary": "数据不完整。",
        "missing_data": ["4H bars"],
        "tool_calls": ["get_fx_evidence_manifest"],
    }

    result = json.loads(
        validator.execute(
            mode="relative_state",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is True
    assert result["checked_evidence_ids"] == ["macro-1"]


def test_hypothesis_accepts_driver_string_and_as_of_metadata(tmp_path) -> None:
    context, validator = _validator(tmp_path)
    output = {
        "schema_version": "2.0",
        "agent_role": "pair_bear",
        "hypothesis_direction": "down",
        "hypothesis_status": "weak",
        "summary": "利差背景仅提供弱下行假设。",
        "causal_chains": [
            {
                "chain_id": "rates-differential",
                "driver": "欧元区与美国政策利率水平差",
                "inference": "美元相对收益背景可能施压 EUR/USD。",
                "transmission": "相对利率影响资金配置。",
                "expected_effect": "推断：构成下行压力。",
                "window": "未来两周",
                "evidence_ids": ["macro-1"],
            }
        ],
        "catalysts": [],
        "market_confirmations": [],
        "strongest_countercase": {
            "statement": "缺少完整4H价格确认。",
            "evidence_ids": ["macro-1"],
        },
        "invalidation_conditions": [
            {
                "condition": "价格转强后失效。",
                "measurement": "4H/1D technical regime",
                "evidence_ids": [],
            }
        ],
        "coverage": {"market": "partial", "macro": "partial"},
        "strength": "weak",
        "missing_data": ["4H bars"],
        "tool_calls": ["get_fx_evidence_manifest"],
        "as_of": "2026-08-13T03:34:05.622998Z",
    }

    result = json.loads(
        validator.execute(
            mode="hypothesis",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is True


def test_hypothesis_assigns_unique_ids_when_model_omits_chain_ids(tmp_path) -> None:
    """Missing IDs must not collapse every causal chain onto one fallback ID."""
    context, validator = _validator(tmp_path)
    output = {
        "schema_version": "2.0",
        "evidence_context_id": context.evidence_context_id,
        "agent_role": "pair_bull",
        "hypothesis_direction": "up",
        "hypothesis_status": "weak",
        "summary": "两个独立但数据不足的上涨传导链。",
        "causal_chains": [
            {
                "observed_facts": [
                    {"statement": "宏观观测一。", "evidence_ids": ["macro-1"]}
                ],
                "inference": "增长背景可能改善。",
                "transmission": "相对预期影响资金配置。",
                "expected_effect": "up",
                "window": "未来两周",
            },
            {
                "observed_facts": [
                    {"statement": "宏观观测二。", "evidence_ids": ["macro-1"]}
                ],
                "inference": "风险偏好可能支撑汇率。",
                "transmission": "风险溢价变化传导至现货。",
                "expected_effect": "up",
                "window": "未来两周",
            },
        ],
        "catalysts": [],
        "market_confirmations": [],
        "strongest_countercase": [
            {"statement": "价格确认不足。", "evidence_ids": ["macro-1"]}
        ],
        "invalidation_conditions": ["若相对宏观背景反转则失效"],
        "coverage": {
            "domains": ["macro"],
            "evidence_family_ids": ["macro-family"],
            "limitations": ["4H bars unavailable"],
        },
        "strength": "low",
        "missing_data": ["4H bars"],
        "tool_calls": [],
    }

    result = json.loads(
        validator.execute(
            mode="hypothesis",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is True


def test_hypothesis_normalizes_status_summary_and_explicit_duplicate_ids(
    tmp_path,
) -> None:
    context, validator = _validator(tmp_path)
    output = _supported_bull(context)
    output["hypothesis_status"] = "rejected"
    output["summary"] = {"statement": "模型返回了对象型摘要。"}
    output["causal_chains"].append(dict(output["causal_chains"][0]))
    output["causal_chains"][1]["claim_id"] = output["causal_chains"][0]["claim_id"]
    output["strength"] = "high"

    result = json.loads(
        validator.execute(
            mode="hypothesis",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is True


def test_risk_review_accepts_misplaced_chain_tool_trace(tmp_path) -> None:
    """A model may put an audit trace inside a causal chain when batching output.

    ``tool_calls`` belongs to the argument envelope, not ``CausalChain``.  The
    Risk Officer re-validates the full upstream arguments, so this compatibility
    case must be normalized there instead of blocking the whole DAG.
    """
    context, validator = _validator(tmp_path)

    def hypothesis(role: str, direction: str, claim_id: str) -> dict:
        chain = {
            "claim_id": claim_id,
            "observed_fact": "同一冻结证据中的价格观测。",
            "inference": "推断：该观测仅形成弱假设。",
            "transmission_mechanism": "若持续，可能影响汇率。",
            "expected_effect": direction,
            "effective_window": "未来两周",
            "evidence_ids": ["macro-1"],
        }
        if role == "pair_bear":
            chain["tool_calls"] = [
                {"tool_name": "get_fx_technical_regime", "query_id": "q-technical"}
            ]
        return {
            "schema_version": "2.0",
            "evidence_context_id": context.evidence_context_id,
            "agent_role": role,
            "hypothesis_direction": direction,
            "hypothesis_status": "weak",
            "summary": "弱假设。",
            "causal_chains": [chain],
            "catalysts": [],
            "market_confirmations": [],
            "strongest_countercase": [
                {"statement": "缺少完整确认。", "evidence_ids": ["macro-1"]}
            ],
            "invalidation_conditions": ["价格结构改变"],
            "coverage": {
                "domains": ["macro"],
                "evidence_family_ids": ["macro-family"],
                "limitations": ["技术数据不足"],
            },
            "strength": "low",
            "missing_data": ["4H bars"],
            "tool_calls": [],
        }

    upstream = [
        hypothesis("pair_bull", "up", "bull-1"),
        hypothesis("pair_bear", "down", "bear-1"),
        {
            "schema_version": "2.0",
            "evidence_context_id": context.evidence_context_id,
            "agent_role": "relative_macro_technical",
            "analysis_status": "insufficient_evidence",
            "relative_macro_state": "indeterminate",
            "technical_state": "indeterminate",
            "cross_confirmation": "indeterminate",
            "findings": [],
            "event_state": "unknown",
            "reliability": "low",
            "summary": "数据不足。",
            "missing_data": ["4H bars"],
            "tool_calls": [],
        },
    ]
    risk_review = {
        "evidence_context_id": context.evidence_context_id,
        "approved_claim_ids": [],
        "rejected_claims": [
            {"claim_id": "bull-1", "reason_code": "insufficient", "reason": "数据不足"},
            {"claim_id": "bear-1", "reason_code": "insufficient", "reason": "数据不足"},
        ],
        "duplicate_claim_groups": [],
        "evidence_conflicts": [],
        "risk_level": "medium",
        "allowed_actions": ["wait", "hedge"],
        "risk_limit": {"max_risk_per_trade_pct": 0.5, "basis": "balanced"},
        "required_invalidation_conditions": [],
        "missing_data": ["4H bars"],
        "risk_summary": "只允许等待或对冲。",
    }

    result = json.loads(
        validator.execute(
            mode="risk_review",
            evidence_context_id=context.evidence_context_id,
            output=risk_review,
            upstream_arguments=upstream,
        )
    )

    assert result["valid"] is True


def test_hypothesis_accepts_deployed_compact_chain_shape() -> None:
    """Compatibility with the deployed prompt's id/steps/description shape."""
    output = {
        "schema_version": "2.0",
        "agent_role": "pair_bear",
        "evidence_context_id": "ctx",
        "hypothesis_direction": "down",
        "hypothesis_status": "weak",
        "summary": "弱假设。",
        "causal_chains": [{
            "id": "rates-chain",
            "label": "利差驱动",
            "direction": "down",
            "steps": [
                {"step_type": "observed_fact", "description": "利差扩大", "evidence_ids": ["fxe-1"]},
                {"step_type": "inference", "description": "美元相对占优", "evidence_ids": ["fxe-1"]},
                {"step_type": "transmission", "description": "资金流入美元", "evidence_ids": ["fxe-1"]},
                {"step_type": "window", "description": "未来两周", "evidence_ids": ["fxe-1"]},
            ],
        }],
        "catalysts": [{"description": "利差", "evidence_ids": ["fxe-1"]}],
        "market_confirmations": [{"description": "价格确认", "evidence_ids": ["fxe-1"]}],
        "strongest_countercase": {"description": "技术反弹", "evidence_ids": ["fxe-1"]},
        "invalidation_conditions": [{"id": "i1", "description": "价格反转", "direction": "up", "evidence_ids": ["fxe-1"]}],
        "coverage": {"market": {"evidence_ids": ["fxe-1"]}},
        "strength": "low",
        "missing_data": [{"description": "事件日历缺失"}],
        "tool_calls": [{"call": "get_fx_evidence_manifest", "params": {}}],
    }
    parsed = HypothesisArgumentV2.model_validate(output)
    assert parsed.causal_chains[0].claim_id == "rates-chain"
    assert parsed.causal_chains[0].observed_fact == "利差扩大"
    assert parsed.missing_data == ["事件日历缺失"]
    assert parsed.tool_calls[0].tool_name == "get_fx_evidence_manifest"


def test_hypothesis_downgrades_non_json_optional_shapes_instead_of_crashing() -> None:
    """The four common top-level shape mistakes must not fail Pydantic parsing."""
    output = {
        "schema_version": "2.0",
        "agent_role": "pair_bull",
        "evidence_context_id": "ctx",
        "hypothesis_direction": "up",
        "hypothesis_status": "supported",
        "summary": "模型输出部分不可审计。",
        "causal_chains": ["prose chain without evidence"],
        "catalysts": [],
        "market_confirmations": [],
        "strongest_countercase": [],
        "invalidation_conditions": [{"id": "i", "description": "反向突破", "unexpected": True}],
        "coverage": {"domains": [], "evidence_family_ids": [], "limitations": []},
        "strength": "low",
        "missing_data": {"description": "4H 数据缺失"},
        "tool_calls": {"call": "get_fx_evidence_manifest"},
    }
    parsed = HypothesisArgumentV2.model_validate(output)
    assert parsed.hypothesis_status == "weak"
    assert parsed.causal_chains == []
    assert parsed.missing_data == ["4H 数据缺失"]
    assert parsed.tool_calls[0].tool_name == "get_fx_evidence_manifest"


def test_relative_state_accepts_scalar_tool_calls() -> None:
    parsed = RelativeStateV2.model_validate({
        "schema_version": "2.0",
        "evidence_context_id": "ctx",
        "agent_role": "relative_macro_technical",
        "analysis_status": "insufficient_evidence",
        "relative_macro_state": "indeterminate",
        "technical_state": "indeterminate",
        "cross_confirmation": "indeterminate",
        "findings": [],
        "event_state": "unknown",
        "reliability": "low",
        "summary": "数据不足。",
        "missing_data": {"description": "4H 数据缺失"},
        "tool_calls": {"call": "get_fx_technical_regime"},
    })
    assert parsed.tool_calls[0].tool_name == "get_fx_technical_regime"
    assert parsed.missing_data == ["4H 数据缺失"]
