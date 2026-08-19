"""FX Debate 宏观与新闻证据 Tool 的公开契约测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from src.fx_debate.context import build_evidence_context
from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions
from src.fx_debate.store import FxEvidenceStore
from src.tools.fx_debate_content_tools import (
    GetFxMacroEvidenceTool,
    GetFxNewsEvidenceTool,
)


def _context():
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
    return build_evidence_context(
        request,
        RunOptions(
            request_id="req-macro-news",
            as_of=datetime(2025, 7, 23, 12, tzinfo=timezone.utc),
        ),
    )


class _FakeReader:
    is_configured = True

    def __init__(self, as_of: datetime) -> None:
        self.as_of = as_of

    def get_macro_observations(self, **kwargs: Any) -> dict[str, Any]:
        valid = {
            "relationship_role": "quote_currency",
            "metric_id": "US_INFLATION_CPI_YOY",
            "metric_name": "US CPI YoY",
            "metric_category": "inflation",
            "metric_frequency": "monthly",
            "release_time": self.as_of - timedelta(days=8),
            "frequency": "monthly",
            "value": Decimal("2.7"),
            "previous_value": Decimal("2.6"),
            "forecast_value": Decimal("2.8"),
            "revised_value": None,
            "source": "LSEG",
            "source_identifier": "USCPIYOY",
            "country": "US",
            "region": None,
            "unit": "percent",
        }
        return {
            "observations": [
                {**valid, "release_time": self.as_of + timedelta(minutes=1)},
                valid,
            ],
            "received": kwargs,
        }

    def get_news(self, **kwargs: Any) -> dict[str, Any]:
        valid = {
            "id": 101,
            "article_id": "RTRS-101",
            "source": "LSEG",
            "publish_time": self.as_of - timedelta(hours=2),
            "title": "ECB keeps policy guidance unchanged",
            "content": "Long body that should not be copied into evidence.",
            "summary": "ECB retained its prior guidance.",
            "url": "https://example.test/rtrs-101",
            "language": "en",
            "sentiment_score": Decimal("-0.1"),
            "relevance_score": Decimal("0.9"),
            "keywords": ["ECB", "EUR"],
        }
        return {
            "articles": [
                {**valid, "id": 102, "publish_time": self.as_of + timedelta(minutes=1)},
                valid,
            ],
            "received": kwargs,
        }


def test_macro_tool_preserves_metric_semantics_and_filters_future_rows(
    tmp_path,
) -> None:
    context = _context()
    tool = GetFxMacroEvidenceTool(
        reader=_FakeReader(context.as_of),
        context=context,
        store=FxEvidenceStore(tmp_path, context.evidence_context_id),
    )

    output = json.loads(
        tool.execute(
            evidence_context_id=context.evidence_context_id,
            metric_ids=["US_INFLATION_CPI_YOY"],
        )
    )

    assert output["ok"] is True
    assert output["status"] == "complete"
    assert len(output["evidence"]) == 1
    item = output["evidence"][0]
    assert item["domain"] == "macro"
    assert item["name"] == "US_INFLATION_CPI_YOY"
    assert item["value"] == {
        "actual": 2.7,
        "previous": 2.6,
        "forecast": 2.8,
        "revised": None,
        "relationship_role": "quote_currency",
        "country": "US",
        "region": None,
    }
    assert datetime.fromisoformat(item["observation_time"]) <= context.as_of
    assert item["source_table"] == "public.macro_observations"


def test_news_tool_returns_compact_articles_with_database_record_ids(tmp_path) -> None:
    context = _context()
    tool = GetFxNewsEvidenceTool(
        reader=_FakeReader(context.as_of),
        context=context,
        store=FxEvidenceStore(tmp_path, context.evidence_context_id),
    )

    output = json.loads(tool.execute(evidence_context_id=context.evidence_context_id))

    assert output["ok"] is True
    assert output["status"] == "complete"
    assert len(output["evidence"]) == 1
    item = output["evidence"][0]
    assert item["domain"] == "news"
    assert item["source_record_ids"] == ["101"]
    assert item["value"]["title"] == "ECB keeps policy guidance unchanged"
    assert item["value"]["sentiment_score"] == -0.1
    assert "content" not in item["value"]
    assert datetime.fromisoformat(item["available_time"]) <= context.as_of


def test_empty_internal_results_are_insufficient_evidence(tmp_path) -> None:
    context = _context()

    class _EmptyReader(_FakeReader):
        def get_news(self, **kwargs: Any) -> dict[str, Any]:
            return {"articles": []}

    output = json.loads(
        GetFxNewsEvidenceTool(
            reader=_EmptyReader(context.as_of),
            context=context,
            store=FxEvidenceStore(tmp_path, context.evidence_context_id),
        ).execute(evidence_context_id=context.evidence_context_id)
    )

    assert output["ok"] is True
    assert output["status"] == "insufficient_evidence"
    assert output["evidence"] == []
    assert output["missing_data"] == ["未找到 Context 时间窗内的内部关联新闻"]
