"""数据集目录在线检索的纯函数和受控候选确认测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from dataset_candidate_selector import validate_dataset_model_selection  # noqa: E402
from search_datasets import (  # noqa: E402
    merge_with_rrf,
    resolve_dataset_candidates,
    search_dataset_documents,
)


class DatasetCursor:
    """按 SQL 关键片段返回固定目录行，验证在线模块的阶段衔接。"""

    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []

    def execute(self, query: str, parameters: tuple[object, ...]) -> None:
        if "d.dataset_id = %s" in query:
            self.rows = [
                (
                    7,
                    "LSEG_SPOT_PRICE",
                    "LSEG Spot Price Snapshot",
                    "market_data",
                    "LSEG",
                    "Latest current spot price snapshot",
                    "realtime",
                    "Spot_Price",
                    1.0,
                )
            ]
        elif "search_vector @@" in query:
            self.rows = [
                (
                    7,
                    "LSEG_SPOT_PRICE",
                    "LSEG Spot Price Snapshot",
                    "market_data",
                    "LSEG",
                    "Latest current spot price snapshot",
                    "realtime",
                    "Spot_Price",
                    0.9,
                )
            ]
        elif "similarity(d.dataset_id" in query:
            self.rows = [
                (
                    7,
                    "LSEG_SPOT_PRICE",
                    "LSEG Spot Price Snapshot",
                    "market_data",
                    "LSEG",
                    "Latest current spot price snapshot",
                    "realtime",
                    "Spot_Price",
                    0.8,
                )
            ]
        elif "FROM source.dataset_catalog" in query:
            self.rows = [
                (
                    "LSEG_SPOT_PRICE",
                    "LSEG Spot Price Snapshot",
                    "market_data",
                    "LSEG",
                    "Latest current spot price snapshot",
                    "realtime",
                    "Spot_Price",
                    "get_fx_spot()",
                    "latest_prices",
                    None,
                    None,
                )
            ]
        else:
            self.rows = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def test_rrf_deduplicates_by_dataset_id() -> None:
    """同一个数据集被多路召回时只能保留一条候选。"""

    rows = {
        "exact": [
            (
                7,
                "LSEG_SPOT_PRICE",
                "LSEG Spot Price Snapshot",
                "market_data",
                "LSEG",
                "spot",
                "realtime",
                "Spot_Price",
                1.0,
            )
        ],
        "embedding": [
            (
                99,
                "LSEG_SPOT_PRICE",
                "LSEG Spot Price Snapshot",
                "market_data",
                "LSEG",
                "spot",
                "realtime",
                "Spot_Price",
                0.98,
            )
        ],
    }

    candidates = merge_with_rrf(rows, limit=3)

    assert len(candidates) == 1
    assert candidates[0]["dataset_id"] == "LSEG_SPOT_PRICE"
    assert candidates[0]["matched_by"] == ["embedding", "exact"]


def test_dataset_catalog_resolver_returns_formal_storage_table() -> None:
    """候选 ID 回查 source 后才能得到正式物理表名。"""

    cursor = DatasetCursor()
    candidates = [
        {
            "dataset_id": "LSEG_SPOT_PRICE",
            "dataset_name": "LSEG Spot Price Snapshot",
            "matched_by": ["exact"],
            "rrf_score": 0.01,
        }
    ]

    resolved, counts = resolve_dataset_candidates(cursor, candidates, provider="LSEG")

    assert resolved[0]["resolution_status"] == "resolved"
    assert resolved[0]["eligible_for_next_step"] is True
    assert resolved[0]["storage_table_name"] == "latest_prices"
    assert counts == {"resolved": 1, "provider_mismatch": 0, "not_found": 0}


def test_dataset_search_runs_catalog_stage_after_rrf() -> None:
    """数据集检索结果应包含正式目录回查结果，而不是只返回 AI 候选。"""

    cursor = DatasetCursor()
    events: list[dict[str, object]] = []

    result = search_dataset_documents(
        cursor,
        "LSEG_SPOT_PRICE",
        limit=3,
        use_embedding=False,
        use_candidate_llm=False,
        provider=None,
        expected_provider="LSEG",
        trace_callback=events.append,
    )

    assert result["candidates"][0]["storage_table_name"] == "latest_prices"
    assert result["candidates"][0]["provider"] == "LSEG"
    assert result["provider_requested"] is None
    assert result["provider_expected"] == "LSEG"
    assert result["dataset_resolution"]["status"] == "skipped"
    assert [event["stage"] for event in events] == [
        "dataset_exact_match",
        "dataset_exact_match",
        "dataset_keyword_search",
        "dataset_keyword_search",
        "dataset_pg_trgm_search",
        "dataset_pg_trgm_search",
        "dataset_embedding_search",
        "dataset_embedding_search",
        "dataset_rrf_merge",
        "dataset_rrf_merge",
        "dataset_catalog",
        "dataset_catalog",
        "dataset_candidate_selector",
        "dataset_candidate_selector",
        "dataset_consistency_check",
        "dataset_consistency_check",
    ]


def test_dataset_model_cannot_choose_unknown_dataset() -> None:
    """候选模型不能越过正式目录候选边界生成任意 dataset_id。"""

    candidates = [
        {
            "dataset_id": "LSEG_SPOT_PRICE",
            "provider": "LSEG",
            "eligible_for_next_step": True,
        }
    ]

    try:
        validate_dataset_model_selection(
            {
                "decision": "select",
                "dataset_id": "source.latest_prices",
                "confidence": 1,
                "reason": "越权测试",
            },
            candidates,
        )
    except ValueError as exc:
        assert "候选列表之外" in str(exc)
    else:
        raise AssertionError("未知 dataset_id 不应通过模型输出校验")
