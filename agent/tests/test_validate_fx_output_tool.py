"""FX Debate 结构、证据引用和风控约束校验测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.fx_debate.context import build_evidence_context
from src.fx_debate.models import (
    EvidenceItem,
    ResolvedFxDebateRequest,
    RunOptions,
)
from src.fx_debate.store import FxEvidenceStore
from src.tools.validate_fx_output_tool import ValidateFxOutputTool


def _context():
    request = ResolvedFxDebateRequest(
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
    )
    return build_evidence_context(
        request,
        RunOptions(
            request_id="req-validate",
            as_of=datetime(2025, 7, 23, 12, tzinfo=timezone.utc),
            risk_profile="balanced",
        ),
    )


def _store_with_evidence(tmp_path, context):
    store = FxEvidenceStore(tmp_path, context.evidence_context_id)
    store.register(
        [
            EvidenceItem(
                evidence_id="fxe-one",
                evidence_context_id=context.evidence_context_id,
                domain="technical",
                name="return_20",
                timeframe="1D",
                value=0.02,
                unit="ratio",
                observation_time=context.as_of,
                available_time=context.as_of,
                source="LSEG",
                source_identifier="EUR=",
                source_table="public.market_bars",
                calculation="close[-1] / close[-21] - 1",
                quality_status="fresh",
            )
        ]
    )
    return store


def _argument(context, role="pair_bull", claim_prefix="bull"):
    return {
        "schema_version": "1.0",
        "evidence_context_id": context.evidence_context_id,
        "agent_role": role,
        "analysis_status": "complete",
        "stance": (
            "BULL"
            if role == "pair_bull"
            else "BEAR" if role == "pair_bear" else "NEUTRAL"
        ),
        "summary": "基于同一 Evidence Context 的测试论证。",
        "claims": [
            {
                "claim_id": f"{claim_prefix}_c1",
                "statement": "20 期价格变化支持当前判断。",
                "evidence_ids": ["fxe-one"],
                "reasoning": "价格变化由同一时间窗内收盘价计算。",
                "impact": (
                    "positive"
                    if role == "pair_bull"
                    else "negative" if role == "pair_bear" else "mixed"
                ),
                "horizon_relevance": "high",
            },
            {
                "claim_id": f"{claim_prefix}_c2",
                "statement": "该指标仍需结合风险边界。",
                "evidence_ids": ["fxe-one"],
                "reasoning": "单一技术指标不足以决定仓位。",
                "impact": "mixed",
                "horizon_relevance": "medium",
            },
        ],
        "counter_evidence": [
            {
                "evidence_ids": ["fxe-one"],
                "explanation": "同一指标也可能已过度延伸。",
            }
        ],
        "analysis_sections": (
            {
                "relative_macro_assessment": "宏观中性。",
                "technical_assessment": "技术方向待确认。",
                "cross_confirmation": "暂未形成同向确认。",
            }
            if role == "relative_macro_technical"
            else None
        ),
        "trade_case": {
            "action": "wait",
            "entry_zone": None,
            "stop_loss": None,
            "targets": [],
        },
        "invalidation_conditions": ["20 期价格变化转为反向"],
        "confidence": 0.55,
        "missing_data": [],
        "tool_calls": [
            {"tool_name": "get_fx_market_evidence", "query_id": "fxq-market"},
            {"tool_name": "get_fx_macro_evidence", "query_id": "fxq-macro"},
            {"tool_name": "get_fx_news_evidence", "query_id": "fxq-news"},
        ],
    }


def test_argument_validation_checks_registered_evidence(tmp_path) -> None:
    context = _context()
    tool = ValidateFxOutputTool(
        context=context,
        store=_store_with_evidence(tmp_path, context),
    )

    result = json.loads(
        tool.execute(
            mode="argument",
            evidence_context_id=context.evidence_context_id,
            output=_argument(context),
        )
    )

    assert result == {
        "valid": True,
        "mode": "argument",
        "errors": [],
        "warnings": [],
        "checked_evidence_ids": ["fxe-one"],
    }


def test_argument_validation_rejects_unknown_evidence_id(tmp_path) -> None:
    context = _context()
    output = _argument(context)
    output["claims"][0]["evidence_ids"] = ["not-registered"]
    tool = ValidateFxOutputTool(
        context=context,
        store=_store_with_evidence(tmp_path, context),
    )

    result = json.loads(
        tool.execute(
            mode="argument",
            evidence_context_id=context.evidence_context_id,
            output=output,
        )
    )

    assert result["valid"] is False
    assert any(error["code"] == "EVIDENCE_NOT_FOUND" for error in result["errors"])


def test_decision_validation_enforces_risk_action_and_conditions(tmp_path) -> None:
    context = _context()
    arguments = [
        _argument(context, "pair_bull", "bull"),
        _argument(context, "pair_bear", "bear"),
        _argument(context, "relative_macro_technical", "mt"),
    ]
    risk_review = {
        "evidence_context_id": context.evidence_context_id,
        "approved_claim_ids": ["bull_c1", "bear_c1", "mt_c1"],
        "rejected_claims": [],
        "duplicate_claim_groups": [],
        "evidence_conflicts": [],
        "risk_level": "medium",
        "allowed_actions": ["wait", "hedge"],
        "risk_limit": {
            "max_risk_per_trade_pct": 0.5,
            "basis": "方向证据冲突。",
        },
        "required_invalidation_conditions": ["价格突破后重新评估"],
        "missing_data": [],
        "risk_summary": "只允许等待或对冲。",
    }
    decision = {
        "evidence_context_id": context.evidence_context_id,
        "canonical_symbol": "EURUSD",
        "display_symbol": "EUR/USD",
        "requested_symbol": "EUR/USD",
        "inverted": False,
        "direction_semantics": "long EURUSD = 买入 EUR、卖出 USD。",
        "decision": "long",
        "confidence": 0.55,
        "horizon_days": 14,
        "scenario_probabilities": {"bull": 0.4, "base": 0.35, "bear": 0.25},
        "thesis": "测试决策。",
        "adopted_claim_ids": ["bull_c1"],
        "rejected_claim_ids": ["bear_c1"],
        "key_evidence_ids": ["fxe-one"],
        "trade_plan": {
            "entry_zone": [1.08, 1.09],
            "stop_loss": 1.07,
            "targets": [1.11],
        },
        "risk_assessment": "方向冲突。",
        "invalidation_conditions": [],
        "missing_data": [],
        "data_as_of": context.as_of.isoformat(),
        "next_review_trigger": "价格突破后重新评估。",
    }
    tool = ValidateFxOutputTool(
        context=context,
        store=_store_with_evidence(tmp_path, context),
    )

    result = json.loads(
        tool.execute(
            mode="decision",
            evidence_context_id=context.evidence_context_id,
            output=decision,
            upstream_arguments=arguments,
            risk_review=risk_review,
        )
    )

    codes = {error["code"] for error in result["errors"]}
    assert result["valid"] is False
    assert "ACTION_NOT_ALLOWED" in codes
    assert "REQUIRED_INVALIDATION_MISSING" in codes
