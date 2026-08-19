"""Swarm worker 到 Evidence Context/Store 的可信注入测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.fx_debate.context import build_evidence_context
from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions
from src.fx_debate.runtime_context import resolve_runtime_resources
from src.swarm.models import SwarmRun
from src.swarm.runtime import _worker_template_vars


def test_worker_artifact_dir_prefers_trusted_context_from_owning_run_json(tmp_path) -> None:
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
    context = build_evidence_context(
        request,
        RunOptions(
            request_id="req-runtime",
            as_of=datetime(2025, 7, 23, 12, tzinfo=timezone.utc),
        ),
    )
    run_root = tmp_path / "swarm-run"
    worker_dir = run_root / "artifacts" / "pair_bull"
    worker_dir.mkdir(parents=True)
    (run_root / "run.json").write_text(
        json.dumps(
            {
                "user_vars": {"target": "EURUSD", "timeframe": "2 weeks; 4H/1D", "goal": "测试"},
                "trusted_context": {"evidence_context": context.model_dump(mode="json")},
            }
        ),
        encoding="utf-8",
    )

    loaded_context, store = resolve_runtime_resources(worker_dir)

    assert loaded_context == context
    assert store.run_root == run_root
    assert store.evidence_context_id == context.evidence_context_id


def test_worker_artifact_dir_supports_legacy_context_in_user_variables(tmp_path) -> None:
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
    context = build_evidence_context(
        request,
        RunOptions(as_of=datetime(2025, 7, 23, 12, tzinfo=timezone.utc)),
    )
    run_root = tmp_path / "legacy-swarm-run"
    worker_dir = run_root / "artifacts" / "pair_bull"
    worker_dir.mkdir(parents=True)
    (run_root / "run.json").write_text(
        json.dumps({"user_vars": {"evidence_context_json": context.model_dump_json()}}),
        encoding="utf-8",
    )

    loaded_context, _store = resolve_runtime_resources(worker_dir)

    assert loaded_context == context


def test_fx_worker_receives_internal_context_without_persisting_it_as_public_vars() -> None:
    run = SwarmRun(
        id="swarm-fx-context",
        preset_name="fx_debate_team",
        user_vars={
            "target": "EUR/USD",
            "timeframe": "2 weeks; 4H/1D",
            "goal": "测试。",
        },
        agents=[],
        tasks=[],
        created_at="2026-08-03T00:00:00+00:00",
        trusted_context={
            "resolved_request_json": "{\"canonical_symbol\": \"EURUSD\"}",
            "evidence_context_json": "{\"evidence_context_id\": \"fxctx-1\"}",
            "evidence_context_id": "fxctx-1",
        },
    )

    worker_vars = _worker_template_vars(run)

    assert run.user_vars == {
        "target": "EUR/USD",
        "timeframe": "2 weeks; 4H/1D",
        "goal": "测试。",
    }
    assert worker_vars["evidence_context_id"] == "fxctx-1"
    assert worker_vars["resolved_request_json"] != run.user_vars.get(
        "resolved_request_json"
    )
