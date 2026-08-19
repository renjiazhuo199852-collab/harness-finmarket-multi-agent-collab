"""金融工具检索入口的纯函数测试。"""

from __future__ import annotations

from datetime import date
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from candidate_selector import validate_model_selection
from search_instruments import (
    merge_with_rrf,
    resolve_instrument_candidates,
    resolve_instrument_identifiers,
    search_instrument_documents,
)


class FakeCursor:
    """只提供 resolver 测试需要的数据库游标行为。"""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.executed_parameters: tuple[object, ...] | None = None

    def execute(self, _query: str, parameters: tuple[object, ...]) -> None:
        self.executed_parameters = parameters

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class TraceSearchCursor:
    """按照 SQL 关键片段返回固定行，专门验证在线阶段追踪事件顺序。"""

    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []

    def execute(self, query: str, _parameters: tuple[object, ...]) -> None:
        if "canonical_symbol = %s" in query:
            self.rows = []
        elif "search_vector @@" in query:
            self.rows = [(1, "EUR/USD", "EUR/USD Spot", "EUR/USD spot", 0.9)]
        elif "similarity(" in query:
            self.rows = [(1, "EUR/USD", "EUR/USD Spot", "EUR/USD spot", 0.8)]
        elif "FROM source.instrument_master" in query:
            self.rows = [
                (
                    "FX_EURUSD",
                    "EUR/USD",
                    "FX",
                    "EUR/USD Spot",
                    "EUR/USD spot exchange rate",
                    "active",
                )
            ]
        else:
            self.rows = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def test_rrf_keeps_candidates_from_multiple_retrieval_methods() -> None:
    """相同 canonical_symbol 即使来自不同文档 ID，也应合并为一条候选。"""

    rows = {
        "pg_trgm": [(59, "EUR/USD", "EUR/USD Spot", "spot", 0.5)],
        "embedding": [(999, "EUR/USD", "EUR/USD Spot", "spot", 0.9)],
    }
    candidates = merge_with_rrf(rows, limit=1)

    assert len(candidates) == 1
    assert candidates[0]["document_id"] == 59
    assert candidates[0]["matched_by"] == ["embedding", "pg_trgm"]


def test_resolver_adds_instrument_id_and_status() -> None:
    """候选回查 master 后，应得到正式 ID 和可继续查询的状态。"""

    candidates = [
        {
            "document_id": 59,
            "canonical_symbol": "EUR/USD",
            "name": "EUR/USD Spot",
            "description": "spot",
            "matched_by": ["pg_trgm"],
            "method_scores": {"pg_trgm": 0.5},
            "rrf_score": 0.01,
        }
    ]
    cursor = FakeCursor(
        [
            (
                "FX_EURUSD",
                "EUR/USD",
                "FX",
                "EUR/USD Spot",
                "EUR/USD spot exchange rate",
                "active",
            )
        ]
    )

    resolved, counts = resolve_instrument_candidates(cursor, candidates)

    assert resolved[0]["instrument_id"] == "FX_EURUSD"
    assert resolved[0]["status"] == "active"
    assert resolved[0]["resolution_status"] == "resolved"
    assert resolved[0]["eligible_for_next_step"] is True
    assert counts == {"resolved": 1, "inactive": 0, "not_found": 0}


def test_model_selection_cannot_choose_outside_active_candidates() -> None:
    """模型只能选择候选列表中的 active instrument_id。"""

    candidates = [
        {
            "canonical_symbol": "EUR/USD",
            "instrument_id": "FX_EURUSD",
            "status": "active",
        }
    ]
    result = validate_model_selection(
        {
            "decision": "select",
            "instrument_id": "FX_EURUSD",
            "confidence": 0.98,
            "reason": "匹配欧元兑美元",
        },
        candidates,
    )

    assert result["decision"] == "select"
    assert result["instrument_id"] == "FX_EURUSD"


def test_identifier_resolver_checks_effective_date() -> None:
    """只返回指定日期已经生效且尚未过期的供应商标识。"""

    cursor = FakeCursor(
        [
            ("FX_EURUSD", "LSEG", "RIC", "EUR=", date(2024, 1, 1), None),
        ]
    )

    result = resolve_instrument_identifiers(
        cursor,
        "FX_EURUSD",
        as_of_date=date(2026, 8, 9),
    )

    assert result["status"] == "resolved"
    assert result["selected"]["provider"] == "LSEG"
    assert result["selected"]["identifier"] == "EUR="


def test_search_pipeline_emits_stage_trace_without_model_calls() -> None:
    """关闭外部模型时，程序检索阶段仍应逐模块发出可观察事件。"""

    cursor = TraceSearchCursor()
    events: list[dict[str, object]] = []
    result = search_instrument_documents(
        cursor,
        "EURUSD",
        limit=3,
        use_embedding=False,
        use_candidate_llm=False,
        trace_callback=events.append,
    )

    assert result["candidates"][0]["instrument_id"] == "FX_EURUSD"
    assert [event["stage"] for event in events] == [
        "exact_match",
        "exact_match",
        "keyword_search",
        "keyword_search",
        "pg_trgm_search",
        "pg_trgm_search",
        "embedding_search",
        "embedding_search",
        "rrf_merge",
        "rrf_merge",
        "instrument_master",
        "instrument_master",
        "candidate_selector",
        "candidate_selector",
        "instrument_identifier",
        "instrument_identifier",
    ]
    assert all(event["status"] in {"running", "completed"} for event in events)
