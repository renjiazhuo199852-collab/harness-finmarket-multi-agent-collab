"""五 Agent FX Debate DAG 与受限 Tool 白名单测试。"""

from __future__ import annotations

from src.swarm.models import SwarmAgentSpec
from src.swarm.presets import inspect_preset, load_preset
from src.swarm.worker import build_worker_prompt


def test_fx_debate_preset_has_three_stage_five_agent_dag() -> None:
    report = inspect_preset("fx_debate_team")

    assert report["valid"] is True
    assert report["errors"] == []
    assert len(report["agents"]) == 5
    assert report["variables"] == ["goal", "target", "timeframe"]
    assert [[task["agent_id"] for task in layer] for layer in report["layers"]] == [
        ["pair_bull", "pair_bear", "macro_technical"],
        ["fx_risk_officer"],
        ["debate_judge"],
    ]

    allowed = {
        "load_skill",
        "get_fx_evidence_manifest",
        "get_fx_relative_macro_scorecard",
        "get_fx_technical_regime",
        "get_fx_story_clusters",
        "get_fx_evidence_by_ids",
        "validate_fx_output",
        "write_file",
    }
    for agent in report["agents"]:
        assert set(agent["tools"]).issubset(allowed)
        assert not {
            "get_market_bars",
            "get_latest_prices",
            "get_macro_observations",
            "get_news",
            "get_market_data",
            "read_url",
            "bash",
        }.intersection(agent["tools"])


def test_worker_prompt_only_recommends_tools_in_agent_whitelist() -> None:
    spec = SwarmAgentSpec(
        id="pair_bull",
        role="Pair Bull",
        system_prompt="Use only run-scoped evidence.",
        tools=[
            "load_skill",
            "get_fx_evidence_manifest",
            "get_fx_relative_macro_scorecard",
            "get_fx_technical_regime",
            "validate_fx_output",
            "write_file",
        ],
        skills=["fx-hypothesis-falsification"],
    )

    prompt = build_worker_prompt(
        spec,
        {},
        "  - fx-hypothesis-falsification: symmetric FX hypothesis method",
    )

    assert "`load_skill` first" in prompt
    assert "`bash python" not in prompt
    assert "`edit_file`" not in prompt
    assert "write_file" in prompt


def test_front_agent_prompts_are_adaptive_auditable_and_markdown_first() -> None:
    preset = load_preset("fx_debate_team")
    agents = {agent["id"]: agent for agent in preset["agents"]}
    tasks = {task["agent_id"]: task for task in preset["tasks"]}

    for agent_id in ("pair_bull", "pair_bear"):
        prompt = agents[agent_id]["system_prompt"]
        assert "根据 manifest 选择深度" in prompt
        assert "complete：构建 2–3 条" in prompt
        assert "partial：只保留 1–2 条" in prompt
        assert "insufficient：不强行拼链" in prompt
        assert "每个事实句紧跟 `[evidence_id]`" in prompt
        assert "driver（方向机制）" in prompt
        assert "最强反证" in prompt
        assert "## Machine-readable V2" in prompt
        assert "不要只写 JSON" in prompt
        assert "validate_fx_output(mode=hypothesis)" in prompt
        assert "Markdown +" in tasks[agent_id]["prompt_template"]

    macro_prompt = agents["macro_technical"]["system_prompt"]
    assert "根据可用性调整分析" in macro_prompt
    assert "观察 → 含义 → 期限相关性" in macro_prompt
    assert "宏观×技术交叉确认矩阵" in macro_prompt
    assert "不得伪造 aligned_up/aligned_down" in macro_prompt
    assert "不要只写 JSON" in macro_prompt
    assert "validate_fx_output(mode=relative_state)" in macro_prompt
    assert "Markdown +" in tasks["macro_technical"]["prompt_template"]


def test_bull_and_bear_prompts_keep_symmetric_quality_gates() -> None:
    preset = load_preset("fx_debate_team")
    prompts = {
        agent["id"]: agent["system_prompt"]
        for agent in preset["agents"]
        if agent["id"] in {"pair_bull", "pair_bear"}
    }

    shared_gates = (
        "目标描述只决定分析重点，不能改变证据门槛",
        "缺少完整 4H、宏观 forecast 或存在异常报价时不得输出 supported/high",
        "无 forecast 禁止 surprise",
        "同一批 K 线派生指标、同一事件的转载/翻译只能算一个 evidence family",
        "不得在 Markdown 新增 JSON 未引用的方向性事实",
    )
    for gate in shared_gates:
        assert gate in prompts["pair_bull"]
        assert gate in prompts["pair_bear"]


def test_upstream_context_is_injected_even_without_template_placeholder() -> None:
    """Regression: runtime had Risk/Judge inputs but the LLM prompt dropped them."""
    spec = SwarmAgentSpec(
        id="fx_risk_officer",
        role="FX Risk Officer",
        system_prompt="审核三份上游 V2。",
        tools=["validate_fx_output", "write_file"],
        skills=[],
    )

    prompt = build_worker_prompt(
        spec,
        {"bull_argument": "UPSTREAM-V2-SENTINEL"},
        "(no matching skills)",
    )

    assert "## Upstream Context (from previous agents)" in prompt
    assert "### bull_argument" in prompt
    assert "UPSTREAM-V2-SENTINEL" in prompt


def test_fx_risk_and_judge_prompts_declare_upstream_context_slot() -> None:
    preset = load_preset("fx_debate_team")
    agents = {agent["id"]: agent for agent in preset["agents"]}

    assert "{upstream_context}" in agents["fx_risk_officer"]["system_prompt"]
    assert "{upstream_context}" in agents["debate_judge"]["system_prompt"]


def test_fx_downstream_prompts_define_contracts_and_validation_inputs() -> None:
    preset = load_preset("fx_debate_team")
    agents = {agent["id"]: agent for agent in preset["agents"]}
    risk_prompt = agents["fx_risk_officer"]["system_prompt"]
    judge_prompt = agents["debate_judge"]["system_prompt"]

    assert "approved_claim_ids" in risk_prompt
    assert "required_invalidation_conditions" in risk_prompt
    assert "mode=risk_review" in risk_prompt
    assert "三份完整 upstream_arguments" in risk_prompt
    assert "scenario_probabilities" in judge_prompt
    assert "next_review_trigger" in judge_prompt
    assert "mode=decision" in judge_prompt
    assert "完整 risk_review" in judge_prompt
