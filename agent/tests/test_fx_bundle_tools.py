"""Frozen EvidenceBundle Tool and scoped Skill tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.agent.skills import SkillsLoader
from src.fx_debate.context import build_evidence_context
from src.fx_debate.evidence_factory import FxEvidenceFactory
from src.fx_debate.evidence_sources import RawFxSnapshot
from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions
from src.tools.fx_debate_bundle_tools import (
    GetFxEvidenceManifestTool,
    GetFxStoryClustersTool,
)
from src.tools.load_skill_tool import LoadSkillTool


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


class _Source:
    def load(self, context):
        return RawFxSnapshot(
            source_name="test",
            prices=[
                {
                    "price_time": context.as_of,
                    "last": 1.1,
                    "bid": 1.09,
                    "ask": 1.11,
                    "mid": 1.1,
                }
            ],
        )


def test_bundle_tool_reads_runtime_owned_bundle_without_requerying(tmp_path) -> None:
    context = _context()
    bundle = FxEvidenceFactory().build(context, _Source())
    run_root = tmp_path / "run"
    worker_dir = run_root / "artifacts" / "pair_bull"
    worker_dir.mkdir(parents=True)
    (run_root / "run.json").write_text(
        json.dumps(
            {
                "trusted_context": {
                    "evidence_context_json": context.model_dump_json(),
                    "evidence_bundle_json": bundle.model_dump_json(),
                }
            }
        ),
        encoding="utf-8",
    )

    result = json.loads(
        GetFxEvidenceManifestTool().execute(
            evidence_context_id=context.evidence_context_id,
            run_dir=str(worker_dir),
        )
    )

    assert result["ok"] is True
    assert result["status"] == "partial"
    assert result["evidence_ids"] == []
    assert result["data"]["event_state"] == "unknown"
    assert result["data"]["market"]["status"] == "insufficient_evidence"

    stories = json.loads(
        GetFxStoryClustersTool().execute(
            evidence_context_id=context.evidence_context_id,
            run_dir=str(worker_dir),
        )
    )
    assert stories["status"] == "insufficient"
    assert stories["evidence_ids"] == []


def test_load_skill_rejects_names_outside_agent_assignment() -> None:
    tool = LoadSkillTool(
        skills_loader=SkillsLoader(),
        allowed_names={"fx-hypothesis-falsification"},
    )

    denied = json.loads(tool.execute(name="technical-basic"))
    allowed = json.loads(tool.execute(name="fx-hypothesis-falsification"))

    assert denied["status"] == "error"
    assert denied["code"] == "SKILL_NOT_ALLOWED"
    assert allowed["status"] == "ok"
