"""AI 目录检索、精确别名、RRF 和 Embedding 降级测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.ai_query.catalog_search import AICatalogSearch, EmbeddingUnavailable
from src.config.env_schema import AIQueryConfig


def _config() -> AIQueryConfig:
    """返回只用于单元测试的本机 AI 查询配置。"""
    return AIQueryConfig(
        enabled=True,
        host="127.0.0.1",
        port=5432,
        database="icbc_finmarket_ai",
        user="postgres",
        password="test-password",
    )


class _Embedding:
    """记录查询文本并返回合法的 2048 维测试向量。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.questions: list[str] = []
        self.fail = fail

    def embed(self, text: str) -> list[float]:
        self.questions.append(text)
        if self.fail:
            raise EmbeddingUnavailable("test embedding failure")
        return [0.0] * 2048


class _CatalogClient:
    """按 SQL 语义返回固定目录候选，不连接真实数据库。"""

    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def fetch_all(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        if "source.instrument_master" in query:
            return [{"instrument_id": "FX_EURUSD"}]
        if "ai.semantic_relations" in query:
            return [
                {
                    "doc_id": "field:LSEG_SPOT_PRICE.PRICE_TIME",
                    "doc_type": "field",
                    "title": "PRICE Time",
                    "dataset_id": "LSEG_SPOT_PRICE",
                    "source_table": "latest_prices",
                    "source_key": None,
                    "source_version": "db_export_0802",
                },
                {
                    "doc_id": "field:LSEG_SPOT_PRICE.BID",
                    "doc_type": "field",
                    "title": "Bid Price",
                    "dataset_id": "LSEG_SPOT_PRICE",
                    "source_table": "latest_prices",
                    "source_key": None,
                    "source_version": "db_export_0802",
                },
                {
                    "doc_id": "field:LSEG_SPOT_PRICE.ASK",
                    "doc_type": "field",
                    "title": "Ask Price",
                    "dataset_id": "LSEG_SPOT_PRICE",
                    "source_table": "latest_prices",
                    "source_key": None,
                    "source_version": "db_export_0802",
                },
                {
                    "doc_id": "field:LSEG_SPOT_PRICE.MID",
                    "doc_type": "field",
                    "title": "Mid Price",
                    "dataset_id": "LSEG_SPOT_PRICE",
                    "source_table": "latest_prices",
                    "source_key": None,
                    "source_version": "db_export_0802",
                },
                {
                    "doc_id": "relation:instrument_to_identifier",
                    "doc_type": "relation",
                    "title": "instrument_to_identifier",
                    "dataset_id": None,
                    "source_table": None,
                    "source_key": None,
                    "source_version": "db_export_0802",
                    "left_table": "instrument_master",
                    "right_table": "instrument_identifier",
                },
                {
                    "doc_id": "relation:identifier_to_latest_prices",
                    "doc_type": "relation",
                    "title": "identifier_to_latest_prices",
                    "dataset_id": None,
                    "source_table": None,
                    "source_key": None,
                    "source_version": "db_export_0802",
                    "left_table": "instrument_identifier",
                    "right_table": "latest_prices",
                },
            ]
        if "WHERE doc_id = ANY" in query:
            return [
                {
                    "doc_id": "instrument:FX_EURUSD",
                    "doc_type": "instrument",
                    "title": "EUR/USD Spot",
                    "dataset_id": None,
                    "source_table": "instrument_master",
                    "source_key": "FX_EURUSD",
                    "source_version": "db_export_0802",
                }
            ]
        if "websearch_to_tsquery" in query:
            return [
                {
                    "doc_id": "dataset:LSEG_SPOT_PRICE",
                    "doc_type": "dataset",
                    "title": "LSEG Spot Price Snapshot",
                    "dataset_id": "LSEG_SPOT_PRICE",
                    "source_table": "latest_prices",
                    "source_key": None,
                    "source_version": "db_export_0802",
                    "score": 0.9,
                }
            ]
        if "embedding <=>" in query:
            return [
                {
                    "doc_id": "field:LSEG_SPOT_PRICE.LAST",
                    "doc_type": "field",
                    "title": "Last Price",
                    "dataset_id": "LSEG_SPOT_PRICE",
                    "source_table": "latest_prices",
                    "source_key": None,
                    "source_version": "db_export_0802",
                    "score": 0.8,
                }
            ]
        raise AssertionError(f"unexpected query: {query}")


def test_search_resolves_eurusd_and_merges_catalog_candidates() -> None:
    """EURUSD 精确命中优先，同时保留数据集和字段候选。"""
    client = _CatalogClient()
    embedding = _Embedding()
    result = AICatalogSearch(
        client, config=_config(), embedding_client=embedding
    ).search("查询 EUR/USD 最新价格", limit=10)

    assert result["retrieval_mode"] == "hybrid"
    assert result["source_versions"] == ["db_export_0802"]
    assert result["candidates"][0]["doc_id"] == "instrument:FX_EURUSD"
    assert result["candidates"][0]["exact_match"] is True
    assert {item["doc_id"] for item in result["candidates"]} == {
        "instrument:FX_EURUSD",
        "dataset:LSEG_SPOT_PRICE",
        "field:LSEG_SPOT_PRICE.LAST",
        "field:LSEG_SPOT_PRICE.PRICE_TIME",
        "field:LSEG_SPOT_PRICE.BID",
        "field:LSEG_SPOT_PRICE.ASK",
        "field:LSEG_SPOT_PRICE.MID",
        "relation:instrument_to_identifier",
        "relation:identifier_to_latest_prices",
    }
    assert {
        item["doc_id"] for item in result["candidates"] if item["doc_type"] == "field"
    } >= {
        "field:LSEG_SPOT_PRICE.PRICE_TIME",
        "field:LSEG_SPOT_PRICE.LAST",
        "field:LSEG_SPOT_PRICE.BID",
        "field:LSEG_SPOT_PRICE.ASK",
        "field:LSEG_SPOT_PRICE.MID",
    }
    assert embedding.questions == ["查询 EUR/USD 最新价格"]


def test_search_falls_back_to_keyword_when_embedding_fails() -> None:
    """外部 Embedding 故障不能阻断明确代码和关键词查询。"""
    client = _CatalogClient()
    result = AICatalogSearch(
        client,
        config=_config(),
        embedding_client=_Embedding(fail=True),
    ).search("EURUSD 最新价格")

    assert result["retrieval_mode"] == "keyword_fallback"
    assert result["candidates"][0]["doc_id"] == "instrument:FX_EURUSD"
    assert any("test embedding failure" in warning for warning in result["warnings"])


@pytest.mark.parametrize("value", ["", 0, 51])
def test_search_rejects_invalid_limit(value: Any) -> None:
    """候选数量必须保持在小范围内。"""
    with pytest.raises(ValueError):
        AICatalogSearch(
            _CatalogClient(), config=_config(), embedding_client=_Embedding()
        ).search("EURUSD", limit=value)
