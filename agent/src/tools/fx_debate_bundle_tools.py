"""Read-only tools over the frozen FX Debate EvidenceBundle."""

from __future__ import annotations

import json
from typing import Any, Callable

from src.agent.tools import BaseTool
from src.fx_debate.evidence_factory import EvidenceBundle
from src.fx_debate.runtime_context import resolve_runtime_bundle


class _FxBundleTool(BaseTool):
    repeatable = True
    parameters = {
        "type": "object",
        "properties": {"evidence_context_id": {"type": "string"}},
        "required": ["evidence_context_id"],
    }

    selector: Callable[[EvidenceBundle], Any]
    status_selector: Callable[[EvidenceBundle], str]
    evidence_selector: Callable[[EvidenceBundle], list[str]] = staticmethod(
        lambda bundle: []
    )

    def execute(self, **kwargs: Any) -> str:
        try:
            context, bundle, _ = resolve_runtime_bundle(kwargs.get("run_dir"))
            requested = str(kwargs.get("evidence_context_id") or "")
            if requested != context.evidence_context_id:
                raise ValueError(
                    "Evidence Context ID does not match the owning Swarm run"
                )
            value = self.selector(bundle)
            payload = (
                value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            )
            return json.dumps(
                {
                    "ok": True,
                    "evidence_context_id": context.evidence_context_id,
                    "query_id": f"{self.name}:{context.evidence_context_id}",
                    "status": _public_status(self.status_selector(bundle)),
                    "evidence_ids": self.evidence_selector(bundle),
                    "data": payload,
                },
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            return json.dumps(
                {
                    "ok": False,
                    "evidence_context_id": str(kwargs.get("evidence_context_id") or ""),
                    "error": {"code": "FX_BUNDLE_ERROR", "message": str(exc)},
                },
                ensure_ascii=False,
            )


class GetFxEvidenceManifestTool(_FxBundleTool):
    name = "get_fx_evidence_manifest"
    description = "读取本轮冻结证据包的数据完整度、缺失项、异常项和事件可用状态。"
    selector = staticmethod(lambda bundle: bundle.manifest)
    status_selector = staticmethod(lambda bundle: bundle.manifest.overall_status)


class GetFxRelativeMacroScorecardTool(_FxBundleTool):
    name = "get_fx_relative_macro_scorecard"
    description = "读取本轮冻结的 base-vs-quote 相对宏观维度和可引用 evidence_id。"
    selector = staticmethod(lambda bundle: bundle.relative_macro_scorecard)
    status_selector = staticmethod(
        lambda bundle: bundle.relative_macro_scorecard.status
    )
    evidence_selector = staticmethod(
        lambda bundle: bundle.relative_macro_scorecard.evidence_ids
    )


class GetFxTechnicalRegimeTool(_FxBundleTool):
    name = "get_fx_technical_regime"
    description = "读取确定性计算的 1D/4H 技术状态、指标、质量和 evidence_id。"
    selector = staticmethod(lambda bundle: bundle.technical_regime)
    status_selector = staticmethod(lambda bundle: bundle.technical_regime.status)
    evidence_selector = staticmethod(
        lambda bundle: bundle.technical_regime.evidence_ids
    )


class GetFxStoryClustersTool(_FxBundleTool):
    name = "get_fx_story_clusters"
    description = "读取按标题、标签和时间窗口确定性去重的当前货币对新闻事件簇。"
    selector = staticmethod(
        lambda bundle: [item.model_dump(mode="json") for item in bundle.story_clusters]
    )
    status_selector = staticmethod(lambda bundle: bundle.manifest.news.status)
    evidence_selector = staticmethod(
        lambda bundle: [
            evidence_id
            for story in bundle.story_clusters
            for evidence_id in story.evidence_ids
        ]
    )


def _public_status(status: str) -> str:
    return "insufficient" if status == "insufficient_evidence" else status
