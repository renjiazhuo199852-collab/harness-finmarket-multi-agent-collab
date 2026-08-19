"""Resolve the immutable Evidence Context injected into a Swarm worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.fx_debate.models import EvidenceContext
from src.fx_debate.evidence_factory import EvidenceBundle
from src.fx_debate.store import FxEvidenceStore


def resolve_runtime_resources(
    run_dir: Any,
) -> tuple[EvidenceContext, FxEvidenceStore]:
    """Load context and store from the owning Swarm run, never from Agent text."""
    if not run_dir:
        raise ValueError("run_dir is required inside an FX Debate Swarm")
    worker_dir = Path(str(run_dir)).resolve()
    run_root = next(
        (
            path
            for path in (worker_dir, *worker_dir.parents)
            if (path / "run.json").is_file()
        ),
        None,
    )
    if run_root is None:
        raise ValueError("cannot locate the owning Swarm run.json")

    payload = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    # Prefer the runtime-owned trusted_context written by newer Swarm runs.
    # Fall back to the legacy user_vars field only for replaying old runs;
    # neither value is taken from an Agent tool argument.
    trusted_context = payload.get("trusted_context")
    raw_context = None
    if isinstance(trusted_context, dict):
        raw_context = trusted_context.get("evidence_context_json")
        if raw_context is None:
            raw_context = trusted_context.get("evidence_context")
    if raw_context is None:
        raw_context = payload.get("user_vars", {}).get("evidence_context_json")
    if isinstance(raw_context, str):
        raw_context = json.loads(raw_context)
    context = EvidenceContext.model_validate(raw_context)
    return context, FxEvidenceStore(run_root, context.evidence_context_id)


def resolve_runtime_bundle(
    run_dir: Any,
) -> tuple[EvidenceContext, EvidenceBundle, FxEvidenceStore]:
    """Load the runtime-owned frozen bundle and register its evidence locally."""
    context, store = resolve_runtime_resources(run_dir)
    worker_dir = Path(str(run_dir)).resolve()
    run_root = next(
        (
            path
            for path in (worker_dir, *worker_dir.parents)
            if (path / "run.json").is_file()
        ),
        None,
    )
    if run_root is None:
        raise ValueError("cannot locate the owning Swarm run.json")
    payload = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    trusted = payload.get("trusted_context")
    raw_bundle = (
        trusted.get("evidence_bundle_json") if isinstance(trusted, dict) else None
    )
    if raw_bundle is None:
        raise ValueError("FX Debate trusted context is missing evidence_bundle_json")
    if isinstance(raw_bundle, str):
        raw_bundle = json.loads(raw_bundle)
    bundle = EvidenceBundle.model_validate(raw_bundle)
    if bundle.evidence_context_id != context.evidence_context_id:
        raise ValueError("Evidence Bundle belongs to a different Evidence Context")
    store.register(bundle.evidence)
    return context, bundle, store
