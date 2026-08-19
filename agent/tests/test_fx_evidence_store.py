"""Run-scoped Evidence Item registration and lookup behavior."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.fx_debate.models import EvidenceItem
from src.fx_debate.store import EvidenceConflictError, FxEvidenceStore


def _item(value: float = 1.1) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="MKT-test-001",
        evidence_context_id="fxctx-test-001",
        domain="market",
        name="latest_close",
        timeframe="1D",
        value=value,
        unit="price",
        observation_time=datetime(2026, 7, 24, tzinfo=timezone.utc),
        available_time=datetime(2026, 7, 24, tzinfo=timezone.utc),
        source="LSEG",
        source_identifier="EUR=",
        dataset_id=None,
        source_table="market_bars",
        source_record_ids=[],
        calculation=None,
        quality_status="fresh",
        notes=None,
    )


def test_registered_evidence_is_idempotent_and_only_visible_in_its_context(
    tmp_path,
) -> None:
    store = FxEvidenceStore(tmp_path, "fxctx-test-001")

    assert store.register([_item()]) == ["MKT-test-001"]
    assert store.register([_item()]) == ["MKT-test-001"]

    evidence, missing = store.get(["MKT-test-001", "MKT-missing"])
    assert [item.evidence_id for item in evidence] == ["MKT-test-001"]
    assert missing == ["MKT-missing"]

    other = FxEvidenceStore(tmp_path, "fxctx-other")
    evidence, missing = other.get(["MKT-test-001"])
    assert evidence == []
    assert missing == ["MKT-test-001"]


def test_same_evidence_id_cannot_be_reused_for_different_content(tmp_path) -> None:
    store = FxEvidenceStore(tmp_path, "fxctx-test-001")
    store.register([_item()])

    with pytest.raises(EvidenceConflictError, match="MKT-test-001"):
        store.register([_item(value=1.2)])
