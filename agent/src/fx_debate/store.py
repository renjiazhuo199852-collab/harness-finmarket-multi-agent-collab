"""Run-scoped, file-backed Evidence Item storage."""

from __future__ import annotations

import os
import re
import threading
import uuid
from pathlib import Path
from typing import Callable

from src.fx_debate.models import EvidenceItem, EvidenceQueryResult

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_QUERY_LOCKS: dict[str, threading.Lock] = {}
_QUERY_LOCKS_GUARD = threading.Lock()


class EvidenceConflictError(RuntimeError):
    """Raised when an Evidence ID is reused for different content."""


def _safe_id(value: str, field_name: str) -> str:
    if not value or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must contain only letters, digits, '.', '_' or '-'"
        )
    return value


class FxEvidenceStore:
    """Persist evidence below one Swarm run directory and one context."""

    def __init__(self, run_root: Path, evidence_context_id: str) -> None:
        self.run_root = Path(run_root)
        self.evidence_context_id = _safe_id(evidence_context_id, "evidence_context_id")
        self._evidence_dir = (
            self.run_root
            / "fx_debate"
            / "contexts"
            / self.evidence_context_id
            / "evidence"
        )
        self._query_dir = self._evidence_dir.parent / "queries"

    def register(self, evidence: list[EvidenceItem]) -> list[str]:
        """Register items idempotently and reject conflicting reuse."""
        registered: list[str] = []
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        for item in evidence:
            if item.evidence_context_id != self.evidence_context_id:
                raise ValueError(
                    "Evidence Item belongs to a different Evidence Context"
                )
            evidence_id = _safe_id(item.evidence_id, "evidence_id")
            target = self._evidence_dir / f"{evidence_id}.json"
            payload = item.model_dump_json(indent=2)
            if target.exists():
                current = EvidenceItem.model_validate_json(
                    target.read_text(encoding="utf-8")
                )
                if current != item:
                    raise EvidenceConflictError(
                        f"Evidence ID {evidence_id!r} already has different content"
                    )
            else:
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, target)
            registered.append(evidence_id)
        return registered

    def get(self, evidence_ids: list[str]) -> tuple[list[EvidenceItem], list[str]]:
        """Return requested items in input order plus IDs not found."""
        evidence: list[EvidenceItem] = []
        missing: list[str] = []
        for raw_id in evidence_ids:
            evidence_id = _safe_id(raw_id, "evidence_id")
            path = self._evidence_dir / f"{evidence_id}.json"
            if not path.is_file():
                missing.append(evidence_id)
                continue
            item = EvidenceItem.model_validate_json(path.read_text(encoding="utf-8"))
            if item.evidence_context_id != self.evidence_context_id:
                missing.append(evidence_id)
                continue
            evidence.append(item)
        return evidence, missing

    def list_all(self) -> list[EvidenceItem]:
        """Return the complete frozen evidence set for deterministic validation."""
        if not self._evidence_dir.is_dir():
            return []
        return [
            EvidenceItem.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self._evidence_dir.glob("*.json"))
        ]

    def get_or_create_query(
        self,
        query_id: str,
        builder: Callable[[], EvidenceQueryResult],
    ) -> EvidenceQueryResult:
        """Freeze one deterministic query result for all parallel Agents."""
        safe_query_id = _safe_id(query_id, "query_id")
        target = self._query_dir / f"{safe_query_id}.json"
        lock = _query_lock(target)
        with lock:
            if target.is_file():
                return EvidenceQueryResult.model_validate_json(
                    target.read_text(encoding="utf-8")
                )
            result = builder()
            if result.query_id != safe_query_id:
                raise ValueError("query builder returned a different query_id")
            if result.evidence_context_id != self.evidence_context_id:
                raise ValueError("query result belongs to a different Evidence Context")
            self.register(result.evidence)
            self._query_dir.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            os.replace(temporary, target)
            return result


def _query_lock(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _QUERY_LOCKS_GUARD:
        return _QUERY_LOCKS.setdefault(key, threading.Lock())
