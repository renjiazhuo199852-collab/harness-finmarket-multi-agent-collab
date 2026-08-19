"""Macro and news Evidence Tools for the run-scoped FX Debate."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.fx_debate.analytics import as_utc_datetime
from src.fx_debate.models import EvidenceContext, EvidenceItem, EvidenceQueryResult
from src.market_data_reader import MarketDataReaderError
from src.market_database import MarketDatabaseUnavailable
from src.tools.fx_debate_tools import _FxMarketDataTool, _stable_id


class _FxContentEvidenceTool(_FxMarketDataTool):
    """Shared stable execution boundary for macro and news evidence."""

    def execute(self, **kwargs: Any) -> str:
        """Query the Reader, register evidence, and return a stable envelope."""
        try:
            context, store = self._resources(kwargs)
            query_id = self._query_id(context, kwargs)
            result = store.get_or_create_query(
                query_id,
                lambda: self._query(context, kwargs),
            )
            return result.model_dump_json()
        except (
            ValueError,
            MarketDataReaderError,
            MarketDatabaseUnavailable,
        ) as exc:
            return self._error(kwargs.get("evidence_context_id"), str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the Tool boundary stable
            return self._error(
                kwargs.get("evidence_context_id"),
                f"FX 内容证据查询失败：{exc}",
            )

    def _query(
        self, context: EvidenceContext, kwargs: dict[str, Any]
    ) -> EvidenceQueryResult:
        raise NotImplementedError

    def _query_id(self, context: EvidenceContext, kwargs: dict[str, Any]) -> str:
        raise NotImplementedError


class GetFxMacroEvidenceTool(_FxContentEvidenceTool):
    """Return linked macro observations from the internal PostgreSQL SDK."""

    name = "get_fx_macro_evidence"
    description = (
        "读取当前 FX Debate Evidence Context 正式关联的宏观发布记录，"
        "保留 actual/previous/forecast、Metric ID、发布时间和 evidence_id。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "evidence_context_id": {"type": "string"},
            "metric_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选 Metric ID 白名单；省略则读取 Context 关联指标。",
            },
        },
        "required": ["evidence_context_id"],
    }
    repeatable = True

    def _query_id(self, context: EvidenceContext, kwargs: dict[str, Any]) -> str:
        raw_metric_ids = kwargs.get("metric_ids")
        metric_ids = (
            sorted({str(value) for value in raw_metric_ids})
            if isinstance(raw_metric_ids, list)
            else []
        )
        return _stable_id(
            "fxq",
            context.evidence_context_id,
            "macro",
            ",".join(metric_ids),
        )

    def _query(
        self, context: EvidenceContext, kwargs: dict[str, Any]
    ) -> EvidenceQueryResult:
        raw_metric_ids = kwargs.get("metric_ids")
        metric_ids = (
            sorted({str(value) for value in raw_metric_ids})
            if isinstance(raw_metric_ids, list)
            else None
        )
        provider = context.provider_priority[0]
        payload = self._reader.get_macro_observations(
            symbol=context.canonical_symbol,
            metric_ids=metric_ids,
            source=provider,
            start_date=context.market_start_time.date().isoformat(),
            end_date=context.as_of.date().isoformat(),
            limit=context.macro_observation_limit,
        )
        rows: list[tuple[Any, dict[str, Any]]] = []
        for row in payload.get("observations", []):
            release_time = as_utc_datetime(row["release_time"])
            if release_time <= context.as_of:
                rows.append((release_time, row))
        rows.sort(key=lambda item: item[0], reverse=True)

        evidence = [
            _macro_item(context, release_time, row, provider)
            for release_time, row in rows
        ]
        return _content_result(
            context=context,
            query_id=self._query_id(context, kwargs),
            evidence=evidence,
            missing_message="未找到 Context 时间窗内的内部关联宏观数据",
        )


class GetFxNewsEvidenceTool(_FxContentEvidenceTool):
    """Return compact, linked news evidence from the internal SDK."""

    name = "get_fx_news_evidence"
    description = (
        "读取当前 FX Debate Evidence Context 正式关联的内部新闻；"
        "返回标题、摘要、情绪、相关度和数据库记录 ID，不复制长正文。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "evidence_context_id": {"type": "string"},
        },
        "required": ["evidence_context_id"],
    }
    repeatable = True

    def _query_id(self, context: EvidenceContext, kwargs: dict[str, Any]) -> str:
        del kwargs
        return _stable_id(
            "fxq",
            context.evidence_context_id,
            "news",
            context.news_start_time.isoformat(),
        )

    def _query(
        self, context: EvidenceContext, kwargs: dict[str, Any]
    ) -> EvidenceQueryResult:
        del kwargs
        provider = context.provider_priority[0]
        payload = self._reader.get_news(
            symbol=context.canonical_symbol,
            source=provider,
            start_date=context.news_start_time.date().isoformat(),
            end_date=context.as_of.date().isoformat(),
            limit=context.news_limit,
        )
        rows: list[tuple[Any, dict[str, Any]]] = []
        for row in payload.get("articles", []):
            publish_time = as_utc_datetime(row["publish_time"])
            if publish_time <= context.as_of:
                rows.append((publish_time, row))
        rows.sort(key=lambda item: item[0], reverse=True)

        evidence = [
            _news_item(context, publish_time, row, provider)
            for publish_time, row in rows
        ]
        return _content_result(
            context=context,
            query_id=self._query_id(context, {}),
            evidence=evidence,
            missing_message="未找到 Context 时间窗内的内部关联新闻",
        )


def _macro_item(
    context: EvidenceContext,
    release_time: Any,
    row: dict[str, Any],
    provider: str,
) -> EvidenceItem:
    metric_id = str(row["metric_id"])
    source_identifier = row.get("source_identifier")
    return EvidenceItem(
        evidence_id=_stable_id(
            "fxe",
            context.evidence_context_id,
            "macro",
            metric_id,
            release_time.isoformat(),
            str(source_identifier or ""),
        ),
        evidence_context_id=context.evidence_context_id,
        domain="macro",
        name=metric_id,
        value={
            "actual": _json_number(row.get("value")),
            "previous": _json_number(row.get("previous_value")),
            "forecast": _json_number(row.get("forecast_value")),
            "revised": _json_number(row.get("revised_value")),
            "relationship_role": row.get("relationship_role"),
            "country": row.get("country"),
            "region": row.get("region"),
        },
        unit=str(row.get("unit") or "unknown"),
        observation_time=release_time,
        available_time=release_time,
        source=str(row.get("source") or provider),
        source_identifier=(
            str(source_identifier) if source_identifier is not None else None
        ),
        source_table="public.macro_observations",
        calculation=None,
        quality_status="fresh",
        notes="release_time 同时作为 observation_time 与 available_time。",
    )


def _news_item(
    context: EvidenceContext,
    publish_time: Any,
    row: dict[str, Any],
    provider: str,
) -> EvidenceItem:
    record_id = str(row.get("id") or "")
    article_id = str(row.get("article_id") or "")
    return EvidenceItem(
        evidence_id=_stable_id(
            "fxe",
            context.evidence_context_id,
            "news",
            record_id,
            article_id,
            publish_time.isoformat(),
        ),
        evidence_context_id=context.evidence_context_id,
        domain="news",
        name="news_article",
        value={
            "title": row.get("title"),
            "summary": row.get("summary"),
            "sentiment_score": _json_number(row.get("sentiment_score")),
            "relevance_score": _json_number(row.get("relevance_score")),
            "url": row.get("url"),
            "language": row.get("language"),
        },
        unit="article",
        observation_time=publish_time,
        available_time=publish_time,
        source=str(row.get("source") or provider),
        source_identifier=article_id or None,
        source_table="public.news_articles",
        source_record_ids=[record_id] if record_id else [],
        calculation=None,
        quality_status="fresh",
        notes="publish_time 同时作为 observation_time 与 available_time。",
    )


def _content_result(
    *,
    context: EvidenceContext,
    query_id: str,
    evidence: list[EvidenceItem],
    missing_message: str,
) -> EvidenceQueryResult:
    return EvidenceQueryResult(
        ok=True,
        status="complete" if evidence else "insufficient_evidence",
        evidence_context_id=context.evidence_context_id,
        query_id=query_id,
        data_as_of=max(
            (item.observation_time for item in evidence),
            default=None,
        ),
        evidence=evidence,
        missing_data=[] if evidence else [missing_message],
    )


def _json_number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return float(str(value))
