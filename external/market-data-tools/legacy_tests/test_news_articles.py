"""news_articles 新闻候选路线的 RRF、字段边界和源表回查测试。"""

from __future__ import annotations

from decimal import Decimal
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import news_articles_pipeline as pipeline  # noqa: E402
from news_articles_adapter import query_news_articles  # noqa: E402
from query_intent import QueryRoute  # noqa: E402
from search_news import _embedding_min_score, _trigram_threshold, merge_with_rrf  # noqa: E402


def _search_row(
    document_id: int,
    source_row_id: int,
    article_id: str,
    score: float,
) -> tuple[object, ...]:
    """构造新闻检索模块使用的统一行结构。"""

    return (
        document_id,
        source_row_id,
        article_id,
        "LSEG",
        "2026-08-01T00:00:00+08:00",
        "EUR/USD market headline",
        "A short summary",
        "The full article content",
        "en",
        {"query_tag": "FX"},
        {"topic": ["FX"]},
        score,
    )


class FakeCursor:
    """返回固定源表行，验证适配器不读取 AI 表正文作为最终事实。"""

    def __init__(self) -> None:
        self.parameters: tuple[object, ...] | None = None
        self.statement: str | None = None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchall(self) -> list[tuple[object, ...]]:
        return [
            (
                11,
                "article-1",
                "LSEG",
                "2026-08-01T00:00:00+08:00",
                "EUR/USD headline from source",
                "en",
                Decimal("0.25"),
                {"query_tag": "FX"},
                {"topic": ["FX"]},
                "2026-08-01T01:00:00+08:00",
                "<p>Source article content</p>",
                "Source article summary",
            )
        ]


def _resolved_fields() -> dict[str, object]:
    """构造 dataset_field_catalog 已确认的三个新闻业务字段。"""

    return {
        "status": "resolved",
        "fields": [
            {"field_name": "title", "physical_column_name": "title"},
            {"field_name": "summary", "physical_column_name": "summary"},
            {"field_name": "content", "physical_column_name": "content"},
        ],
    }


def test_news_rrf_deduplicates_by_article_and_source() -> None:
    """同一篇新闻被多路召回时只保留一个候选，并记录多路证据。"""

    candidates = merge_with_rrf(
        {
            "exact": [_search_row(1, 11, "article-1", 1.0)],
            "embedding": [_search_row(9, 11, "article-1", 0.97)],
        },
        limit=3,
    )

    assert len(candidates) == 1
    assert candidates[0]["article_id"] == "article-1"
    assert candidates[0]["matched_by"] == ["embedding", "exact"]


def test_news_rrf_without_limit_keeps_all_candidates() -> None:
    """新闻路线未设置上限时，应保留所有 RRF 去重后的文章。"""

    candidates = merge_with_rrf(
        {
            "exact": [
                _search_row(1, 11, "article-1", 1.0),
                _search_row(2, 12, "article-2", 1.0),
                _search_row(3, 13, "article-3", 1.0),
            ]
        },
        limit=None,
    )

    assert [candidate["article_id"] for candidate in candidates] == [
        "article-1",
        "article-2",
        "article-3",
    ]


def test_news_embedding_min_score_is_configurable(monkeypatch) -> None:
    """语义相关性门槛可配置，但默认值不能变成条数限制。"""

    monkeypatch.delenv("NEWS_EMBEDDING_MIN_SCORE", raising=False)
    assert _embedding_min_score() == 0.40

    monkeypatch.setenv("NEWS_EMBEDDING_MIN_SCORE", "0.55")
    assert _embedding_min_score() == 0.55


def test_news_trigram_threshold_is_configurable(monkeypatch) -> None:
    """新闻模糊检索默认过滤低分字符重叠，并允许部署时重新校准。"""

    monkeypatch.delenv("NEWS_TRIGRAM_MIN_SCORE", raising=False)
    assert _trigram_threshold() == 0.20
    monkeypatch.setenv("NEWS_TRIGRAM_MIN_SCORE", "0.27")
    assert _trigram_threshold() == 0.27


def test_news_adapter_returns_catalog_fields_and_source_metadata() -> None:
    """最终结果的业务字段来自字段目录，文章身份来自源表回查。"""

    cursor = FakeCursor()
    result = query_news_articles(
        cursor,
        [
            {
                "source_row_id": 11,
                "article_id": "article-1",
                "matched_by": ["embedding"],
                "rrf_score": 0.016,
            }
        ],
        {
            "dataset_id": "LSEG_NEWS",
            "storage_table_name": "news_articles",
        },
        _resolved_fields(),
        limit=3,
    )

    assert result["status"] == "resolved"
    assert set(result["rows"][0]["data"]) == {"title", "summary", "content"}
    assert result["rows"][0]["data"]["title"] == "EUR/USD headline from source"
    assert result["rows"][0]["metadata"]["article_id"] == "article-1"
    assert result["rows"][0]["metadata"]["source"] == "LSEG"
    assert cursor.parameters == ([11],)


def test_news_pipeline_keeps_original_subject_as_news_search_input(monkeypatch) -> None:
    """新闻路线使用解析出的主体检索，不把主体改成新闻外键或物理表名。"""

    monkeypatch.setattr(
        pipeline,
        "recognize_query_intent",
        lambda _query: {
            "route": QueryRoute.NEWS_ARTICLES.value,
            "confidence": 1.0,
            "reason": "test",
            "instrument_text": "EURUSD",
            "instrument_search_text": "EURUSD",
            "provider_text": None,
            "time_expression": None,
            "request_text": "相关新闻",
        },
    )
    monkeypatch.setattr(
        pipeline,
        "search_dataset_documents",
        lambda *_args, **_kwargs: {
            "query": "financial news",
            "warnings": [],
            "methods": {},
            "catalog_resolution": {"resolved": 1},
            "candidates": [],
            "model_selection": {"decision": "select", "dataset_id": "LSEG_NEWS"},
            "dataset_resolution": {
                "status": "resolved",
                "dataset_id": "LSEG_NEWS",
                "storage_table_name": "news_articles",
                "provider": "LSEG",
            },
        },
    )
    monkeypatch.setattr(pipeline, "resolve_dataset_fields", lambda *_args, **_kwargs: _resolved_fields())

    observed: dict[str, object] = {}

    def fake_search_news(_cursor: object, query: str, **_kwargs: object) -> dict[str, object]:
        observed["query"] = query
        observed["limit"] = _kwargs.get("limit")
        return {"query": query, "methods": {"exact": 1}, "warnings": [], "candidates": [], "candidate_selection": None}

    monkeypatch.setattr(pipeline, "search_news_documents", fake_search_news)
    def fake_query_news_articles(*_args: object, **kwargs: object) -> dict[str, object]:
        observed["adapter_limit"] = kwargs.get("limit")
        return {"status": "resolved", "rows": [], "row_count": 0}

    monkeypatch.setattr(pipeline, "query_news_articles", fake_query_news_articles)

    result = pipeline.search_news_articles_route(
        object(),
        "查询 EURUSD 的相关新闻",
        requested_route=QueryRoute.NEWS_ARTICLES,
        use_embedding=False,
        use_candidate_llm=False,
    )

    assert observed["query"] == "EURUSD financial news"
    assert observed["limit"] is None
    assert observed["adapter_limit"] is None
    assert result["dataset_resolution"]["dataset_id"] == "LSEG_NEWS"
    assert result["field_resolution"]["status"] == "resolved"
    assert result["news_result"]["status"] == "resolved"
