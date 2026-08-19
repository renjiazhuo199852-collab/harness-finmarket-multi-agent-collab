"""正式服务精简响应的协议测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "front"))

from public_response import build_public_response  # noqa: E402
import server  # noqa: E402


def test_public_response_keeps_flat_business_rows() -> None:
    """最新价格和历史行情只公开适配器生成的业务字段。"""

    result = build_public_response(
        {
            "status": "success",
            "execution": {
                "status": "resolved",
                "adapter": "latest_prices",
                "fields": [{"field_name": "last"}],
                "filters": {"source_identifier": "EUR="},
                "rows": [{"last": "1.1528"}],
                "row_count": 1,
            },
        }
    )

    assert result == {"status": "success", "data": [{"last": "1.1528"}]}


def test_public_response_removes_macro_metadata() -> None:
    """宏观结果只公开 value 等字段目录确认的业务值，不公开指标元数据。"""

    result = build_public_response(
        {
            "status": "success",
            "execution": {
                "status": "resolved",
                "adapter": "macro_observations",
                "rows": [
                    {
                        "data": {"value": "2.8"},
                        "metadata": {
                            "metric_id": "METRIC_US_CPI_YOY",
                            "release_time": "2026-08-01T08:30:00+08:00",
                        },
                    }
                ],
            },
        }
    )

    assert result == {"status": "success", "data": [{"value": "2.8"}]}


def test_public_response_removes_news_metadata() -> None:
    """新闻公开标题、摘要和正文，不公开源表 id、匹配分数等内部信息。"""

    result = build_public_response(
        {
            "status": "success",
            # 兼容独立接口的 adapter 位于完整结果顶层，而不是 execution 内。
            "adapter": "news_articles",
            "execution": {
                "status": "resolved",
                "rows": [
                    {
                        "data": {"title": "EUR/USD rises", "summary": None, "content": "..."},
                        "metadata": {
                            "id": 1,
                            "source": "LSEG",
                            "rrf_score": 0.03,
                        },
                    }
                ],
            },
        }
    )

    assert result == {
        "status": "success",
        "data": [{"title": "EUR/USD rises", "summary": None, "content": "..."}],
    }


def test_public_response_returns_empty_data_for_not_found() -> None:
    """没有事实行仍然是成功完成的查询，调用方只接收空数组。"""

    result = build_public_response(
        {
            "status": "success",
            "execution": {
                "status": "not_found",
                "adapter": "latest_prices",
                "rows": [],
                "row_count": 0,
            },
        }
    )

    assert result == {"status": "success", "data": []}


def test_public_response_exposes_only_rejection_contract() -> None:
    """目录或适配器拒绝时只返回错误码和消息，不返回内部候选对象。"""

    result = build_public_response(
        {
            "status": "rejected",
            "dataset_search": {
                "candidates": [{"dataset_id": "LSEG_NEWS"}],
            },
            "execution": {
                "status": "rejected",
                "code": "DATASET_INTENT_MISMATCH",
                "reason": "用户问题与数据集候选不一致",
                "rows": [],
            },
        }
    )

    assert result == {
        "status": "rejected",
        "data": [],
        "code": "DATASET_INTENT_MISMATCH",
        "message": "用户问题与数据集候选不一致",
    }


def test_unified_http_endpoint_returns_compact_response(
    monkeypatch: Any,
) -> None:
    """普通统一 HTTP 接口不应把完整编排结果直接返回给调用方。"""

    monkeypatch.setattr(
        server,
        "run_unified_search",
        lambda request: {
            "status": "success",
            "dataset_search": {"candidates": [{"dataset_id": "LSEG_SPOT_PRICE"}]},
            "execution": {
                "status": "resolved",
                "adapter": "latest_prices",
                "rows": [{"last": "1.1528"}],
            },
            "routing": {"dataset_id": "LSEG_SPOT_PRICE"},
        },
    )

    response = server.unified_search(server.UnifiedSearchRequest(query="EURUSD"))

    assert response == {"status": "success", "data": [{"last": "1.1528"}]}
