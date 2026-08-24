"""Build one deterministic, run-scoped evidence bundle for the front agents."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.fx_debate.analytics import (
    TECHNICAL_CONFIRMATION_MIN_BARS,
    TECHNICAL_OBSERVATION_MIN_BARS,
    as_utc_datetime,
    normalize_bars,
    technical_metrics,
)
from src.fx_debate.contracts import PresentationSummary
from src.fx_debate.evidence_sources import FxEvidenceSource, RawFxSnapshot
from src.fx_debate.models import EvidenceContext, EvidenceItem

Status = Literal["complete", "partial", "insufficient_evidence"]

# Spot quotes are useful for a current run only while they are reasonably
# close to the run's point-in-time boundary. Stale quotes remain auditable but
# must not be presented as fresh evidence.
_QUOTE_MAX_AGE = timedelta(hours=24)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainManifest(_Model):
    status: Status
    record_count: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvidenceManifest(_Model):
    overall_status: Status
    quote: DomainManifest
    market: DomainManifest
    macro: DomainManifest
    news: DomainManifest
    event_state: Literal["unknown"] = "unknown"
    event_reason: str = "event_calendar_not_connected"


class MacroSignal(_Model):
    dimension: Literal["rates", "growth", "labor", "inflation"]
    relationship: Literal[
        "base_supported", "quote_supported", "balanced", "conditional", "unknown"
    ]
    base_value: float | None = None
    quote_value: float | None = None
    difference: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class RelativeMacroScorecard(_Model):
    status: Status
    signals: list[MacroSignal] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class TechnicalTimeframeState(_Model):
    timeframe: Literal["4H", "1D"]
    state: Literal["bullish", "bearish", "range", "transition", "indeterminate"]
    bar_count: int
    latest_bar_time: datetime | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class TechnicalRegime(_Model):
    status: Status
    timeframes: dict[str, TechnicalTimeframeState]
    quote_quality: Literal["fresh", "stale", "partial", "abnormal", "missing"]
    evidence_ids: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class StoryCluster(_Model):
    story_id: str
    representative_title: str
    publish_time: datetime
    article_ids: list[str]
    tags: list[str]
    language_quality: Literal["declared", "suspect", "unknown"]
    evidence_ids: list[str]


class EvidenceBundle(_Model):
    evidence_context_id: str
    as_of: datetime
    source_name: str
    manifest: EvidenceManifest
    evidence: list[EvidenceItem]
    relative_macro_scorecard: RelativeMacroScorecard
    technical_regime: TechnicalRegime
    story_clusters: list[StoryCluster]
    presentation: PresentationSummary
    # Bounded source rows for the operator preview. Agents only access the
    # derived EvidenceItems through Bundle Tools, not this UI projection.
    raw_preview: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    raw_counts: dict[str, int] = Field(default_factory=dict)


class FxEvidenceFactory:
    """Hide data quality, indicators, macro comparison, and news de-duplication."""

    def build(
        self,
        context: EvidenceContext,
        source: FxEvidenceSource,
    ) -> EvidenceBundle:
        raw = source.load(context)
        evidence: list[EvidenceItem] = []

        quote_items, quote_manifest, quote_quality = _build_quote(context, raw)
        evidence.extend(quote_items)
        technical, technical_items, market_manifest = _build_technical(
            context, raw, quote_quality
        )
        evidence.extend(technical_items)
        macro, macro_items, macro_manifest = _build_macro(context, raw)
        evidence.extend(macro_items)
        stories, news_items, news_manifest = _build_stories(context, raw)
        evidence.extend(news_items)
        presentation = _build_presentation(
            context,
            raw,
            quote_quality=quote_quality,
            technical=technical,
            macro=macro,
            news_manifest=news_manifest,
        )

        core_statuses = {
            quote_manifest.status,
            market_manifest.status,
            macro_manifest.status,
        }
        if core_statuses == {"complete"}:
            overall: Status = "complete"
        elif evidence:
            overall = "partial"
        else:
            overall = "insufficient_evidence"
        return EvidenceBundle(
            evidence_context_id=context.evidence_context_id,
            as_of=context.as_of,
            source_name=raw.source_name,
            manifest=EvidenceManifest(
                overall_status=overall,
                quote=quote_manifest,
                market=market_manifest,
                macro=macro_manifest,
                news=news_manifest,
            ),
            evidence=evidence,
            relative_macro_scorecard=macro,
            technical_regime=technical,
            story_clusters=stories,
            presentation=presentation,
            raw_preview=_raw_preview(raw, context.as_of),
            raw_counts={
                "quote": len(raw.prices),
                "market": len(raw.prices) + len(raw.bars),
                "macro": len(raw.macro),
                "news": len(raw.news),
            },
        )


def _build_presentation(
    context: EvidenceContext,
    raw: RawFxSnapshot,
    *,
    quote_quality: Literal["fresh", "stale", "partial", "abnormal", "missing"],
    technical: TechnicalRegime,
    macro: RelativeMacroScorecard,
    news_manifest: DomainManifest,
) -> PresentationSummary:
    """Build a display summary without promoting incomplete data to signals."""
    usable_evidence: list[str] = []

    pmi_rows = _latest_macro_pair_rows(raw, ("PMI_MANUFACTURING", "PMI_SERVICES"))
    manufacturing = pmi_rows.get("PMI_MANUFACTURING", {})
    services = pmi_rows.get("PMI_SERVICES", {})
    eu_manufacturing = _float(manufacturing.get("EU", {}).get("value"))
    us_manufacturing = _float(manufacturing.get("US", {}).get("value"))
    eu_services = _float(services.get("EU", {}).get("value"))
    us_services = _float(services.get("US", {}).get("value"))
    pmi_supports_us = False
    if eu_manufacturing is not None and us_manufacturing is not None:
        pmi_supports_us = us_manufacturing > eu_manufacturing
        manufacturing_comparison = "高于" if pmi_supports_us else "低于"
        usable_evidence.append(
            f"US PMI {manufacturing_comparison} EU PMI（制造业 "
            f"{us_manufacturing:.2f} vs {eu_manufacturing:.2f}"
            + (
                f"；服务业 {us_services:.2f} vs {eu_services:.2f}"
                if us_services is not None and eu_services is not None
                else ""
            )
            + "，均为历史 actual）"
        )
    elif eu_services is not None and us_services is not None:
        pmi_supports_us = us_services > eu_services
        services_comparison = "高于" if pmi_supports_us else "低于"
        usable_evidence.append(
            f"US 服务业 PMI {services_comparison} EU（{us_services:.2f} vs {eu_services:.2f}，历史 actual）"
        )

    unemployment = _latest_macro_pair_rows(raw, ("UNEMPLOYMENT",))
    eu_unemployment = _float(unemployment.get("UNEMPLOYMENT", {}).get("EU", {}).get("value"))
    us_unemployment = _float(unemployment.get("UNEMPLOYMENT", {}).get("US", {}).get("value"))
    labor_supports_us = False
    if eu_unemployment is not None and us_unemployment is not None:
        labor_supports_us = us_unemployment < eu_unemployment
        labor_comparison = "低于" if labor_supports_us else "高于"
        usable_evidence.append(
            f"US 失业率{labor_comparison} EU（{us_unemployment:.2f}% vs {eu_unemployment:.2f}%，历史 actual）"
        )

    cpi_rows = _latest_macro_pair_rows(raw, ("CPI_YOY",))
    eu_cpi = _float(cpi_rows.get("CPI_YOY", {}).get("EU", {}).get("value"))
    us_cpi = _float(cpi_rows.get("CPI_YOY", {}).get("US", {}).get("value"))
    if eu_cpi is not None and us_cpi is not None:
        cpi_comparison = "高于" if us_cpi > eu_cpi else "低于"
        usable_evidence.append(
            f"US CPI {cpi_comparison} EU（{us_cpi:.2f}% vs {eu_cpi:.2f}%；仅作事实，不代表当前政策方向）"
        )

    if pmi_supports_us or labor_supports_us:
        market_background = "美元历史基本面背景偏强，EUR/USD 宏观背景偏空"
        summary = "宏观背景偏空，但缺少价格和事件确认，不能转化为交易信号"
    elif usable_evidence:
        market_background = "宏观 actual 可读，但不足以形成可执行方向"
        summary = "已有宏观事实可以汇报，但不能形成当前交易信号"
    else:
        market_background = "宏观背景无法确定"
        summary = "当前证据不足以形成方向背景或交易信号"

    daily_state = technical.timeframes.get("1D")
    four_hour_state = technical.timeframes.get("4H")
    daily_count = daily_state.bar_count if daily_state else 0
    four_hour_count = four_hour_state.bar_count if four_hour_state else 0
    if four_hour_count == 0:
        observation_note = (
            f"（1D 已达到 {TECHNICAL_OBSERVATION_MIN_BARS} 根观察门槛，"
            f"完整确认仍需 {TECHNICAL_CONFIRMATION_MIN_BARS} 根）"
            if daily_count >= TECHNICAL_OBSERVATION_MIN_BARS
            else ""
        )
        technical_confirmation = (
            f"无法确认：4H 无数据，1D 仅 {daily_count} 根{observation_note}"
        )
    elif (daily_state and daily_state.state == "indeterminate") or (
        four_hour_state and four_hour_state.state == "indeterminate"
    ):
        technical_confirmation = (
            f"无法确认：4H {four_hour_count} 根，1D {daily_count} 根；"
            f"观察门槛为 {TECHNICAL_OBSERVATION_MIN_BARS} 根，"
            f"完整确认仍需 {TECHNICAL_CONFIRMATION_MIN_BARS} 根"
        )
    else:
        technical_confirmation = "技术状态已计算，但仍需结合新鲜报价确认"

    if daily_state and daily_state.metrics:
        latest_close = daily_state.metrics.get("latest_close")
        return_20 = daily_state.metrics.get("return_20")
        if latest_close is not None:
            fact = f"1D 观察事实：最新收盘 {latest_close:.5f}"
            if return_20 is not None:
                fact += f"，20 根变化 {return_20:+.2%}"
            usable_evidence.append(fact + "（仅作样本事实，不代表趋势确认）")

    limitations: list[str] = []
    macro_times = [_safe_datetime(row.get("release_time")) for row in raw.macro]
    macro_times = [value for value in macro_times if value is not None]
    if macro_times:
        ages = [max(0, (context.as_of - value).days) for value in macro_times]
        age_text = f"{min(ages)}-{max(ages)} 天" if min(ages) != max(ages) else f"{max(ages)} 天"
        limitations.append(f"宏观数据距 as_of {age_text}")
    missing_forecasts = sum(1 for row in raw.macro if _float(row.get("forecast_value")) is None)
    if raw.macro and missing_forecasts:
        limitations.append(f"{missing_forecasts}/{len(raw.macro)} forecast 缺失")
    if macro.status != "complete" and not any(
        signal.relationship != "unknown" for signal in macro.signals
    ):
        limitations.append("宏观相对计分卡无法形成可比方向")
    limitations.extend([
        f"4H bar_count={four_hour_count}",
        f"1D bar_count={daily_count}",
    ])
    if quote_quality != "fresh":
        quote_times = [
            value
            for value in (_safe_datetime(row.get("price_time")) for row in raw.prices)
            if value is not None
        ]
        quote_time = max(quote_times) if quote_times else None
        if quote_time is None:
            limitations.append("quote 不可用")
        else:
            limitations.append(f"quote 过期 {max(0, (context.as_of - quote_time).days)} 天")
    if not raw.news or news_manifest.status == "insufficient_evidence":
        limitations.append("news/event 不可用")
    if not limitations:
        data_quality: Literal["complete", "partial", "degraded"] = "complete"
    elif (four_hour_state and four_hour_state.state == "indeterminate") or quote_quality != "fresh":
        data_quality = "degraded"
    else:
        data_quality = "partial"

    return PresentationSummary(
        market_background=market_background,
        background_strength="low",
        technical_confirmation=technical_confirmation,
        data_quality=data_quality,
        summary=summary,
        usable_evidence=usable_evidence,
        limitations=limitations,
    )


def _latest_macro_pair_rows(
    raw: RawFxSnapshot, metric_ids: tuple[str, ...]
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the latest actual row for each requested metric and country."""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for row in raw.macro:
        metric_id = str(row.get("metric_id") or "").upper()
        matched = next(
            (
                candidate
                for candidate in metric_ids
                if metric_id == candidate
                or metric_id.endswith(f"_{candidate}")
                or (candidate == "CPI_YOY" and metric_id in {"CPI", "CPI_YOY"})
            ),
            None,
        )
        if matched is None:
            continue
        country = str(row.get("country") or "").upper()
        release_time = _safe_datetime(row.get("release_time"))
        if country not in {"EU", "US"} or release_time is None:
            continue
        current = result.setdefault(matched, {}).get(country)
        if current is None or release_time > current["_release_time"]:
            result[matched][country] = {**row, "_release_time": release_time}
    return result


def _safe_datetime(value: Any) -> datetime | None:
    try:
        return as_utc_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None


_CURRENCY_COUNTRY = {
    "EUR": "EU",
    "GBP": "UK",
    "JPY": "JP",
    "AUD": "AU",
    "CAD": "CA",
    "CHF": "CH",
    "NZD": "NZ",
    "USD": "US",
}


def _macro_countries(context: EvidenceContext) -> tuple[str, str]:
    """Map currency codes to the country keys used by the export."""
    return (
        _CURRENCY_COUNTRY.get(
            context.base_currency.upper(), context.base_currency.upper()
        ),
        _CURRENCY_COUNTRY.get(
            context.quote_currency.upper(), context.quote_currency.upper()
        ),
    )


def _raw_preview(
    raw: RawFxSnapshot, as_of: datetime
) -> dict[str, list[dict[str, Any]]]:
    """Project source rows into a small, secret-free UI preview."""
    return {
        "market": [_source_row("quote", row, as_of) for row in raw.prices[:8]]
        + [_source_row("bar", row) for row in raw.bars[:120]],
        "macro": [_source_row("macro", row) for row in raw.macro[:120]],
        "news": [_source_row("news", row) for row in raw.news[:60]],
    }


def _source_row(
    kind: str, row: dict[str, Any], as_of: datetime | None = None
) -> dict[str, Any]:
    """Keep only columns safe and useful for a human table preview."""
    if kind == "quote":
        value = {
            key: _float(row.get(key))
            for key in ("last", "bid", "ask", "mid")
            if _float(row.get(key)) is not None
        }
        return {
            "name": "spot_quote",
            "timeframe": None,
            "value": value,
            "observation_time": _safe_iso(row.get("price_time")),
            "quality_status": _quote_quality(row, as_of),
            "source_table": "latest_prices",
            "source": str(row.get("source") or "unknown"),
            "source_identifier": str(row.get("source_identifier") or ""),
        }
    if kind == "bar":
        value = {
            key: _float(row.get(key))
            for key in ("open", "high", "low", "close")
            if _float(row.get(key)) is not None
        }
        return {
            "name": "market_bar",
            "timeframe": str(row.get("frequency") or "").upper() or None,
            "value": value,
            "observation_time": _safe_iso(row.get("bar_time")),
            "quality_status": "fresh",
            "source_table": "market_bars",
            "source": str(row.get("source") or "unknown"),
            "source_identifier": str(row.get("source_identifier") or ""),
        }
    if kind == "macro":
        return {
            "name": str(row.get("metric_id") or "UNKNOWN").upper(),
            "timeframe": str(row.get("frequency") or "").upper() or None,
            "value": {
                "country": str(row.get("country") or "").upper(),
                "actual": _float(row.get("value")),
                "forecast": _float(row.get("forecast_value")),
                "previous": _float(row.get("previous_value")),
            },
            "observation_time": _safe_iso(row.get("release_time")),
            "quality_status": (
                "fresh" if _float(row.get("forecast_value")) is not None else "partial"
            ),
            "source_table": "macro_observations",
            "source": str(row.get("source") or "unknown"),
            "source_identifier": str(row.get("source_identifier") or ""),
        }
    return {
        "name": "news_article",
        "timeframe": None,
        "value": {
            "article_id": str(row.get("article_id") or row.get("id") or ""),
            "title": str(row.get("title") or ""),
            "tags": str(row.get("related_entities") or ""),
        },
        "observation_time": _safe_iso(row.get("publish_time")),
        "quality_status": "fresh",
        "source_table": "news_articles",
        "source": str(row.get("source") or "unknown"),
        "source_identifier": str(row.get("article_id") or row.get("id") or ""),
    }


def _safe_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return as_utc_datetime(value).isoformat()
    except (TypeError, ValueError):
        return str(value)


def _build_quote(context: EvidenceContext, raw: RawFxSnapshot) -> tuple[
    list[EvidenceItem],
    DomainManifest,
    Literal["fresh", "stale", "partial", "abnormal", "missing"],
]:
    missing = raw.missing_columns.get("prices", [])
    if not raw.prices:
        return (
            [],
            DomainManifest(
                status="insufficient_evidence",
                missing_fields=missing,
                warnings=[f"{context.display_symbol} quote is unavailable"],
            ),
            "missing",
        )
    row = max(raw.prices, key=lambda item: as_utc_datetime(item["price_time"]))
    price_time = as_utc_datetime(row["price_time"])
    values = {name: _float(row.get(name)) for name in ("last", "bid", "ask", "mid")}
    abnormal = bool(
        (values["last"] is not None and values["last"] <= 0)
        or (values["mid"] is not None and values["mid"] <= 0)
        or (
            values["bid"] is not None
            and values["ask"] is not None
            and values["bid"] > values["ask"]
        )
        or (
            values["bid"] is not None
            and values["mid"] is not None
            and values["ask"] is not None
            and not values["bid"] <= values["mid"] <= values["ask"]
        )
    )
    partial = any(values[name] is None for name in ("bid", "ask", "mid"))
    stale = context.as_of - price_time > _QUOTE_MAX_AGE
    quality: Literal["fresh", "stale", "partial", "abnormal", "missing"] = (
        "abnormal"
        if abnormal
        else "stale" if stale else "partial" if partial else "fresh"
    )
    item = EvidenceItem(
        evidence_id=_id("quote", context.evidence_context_id, price_time.isoformat()),
        evidence_context_id=context.evidence_context_id,
        evidence_family_id=f"quote:{price_time.isoformat()}",
        domain="market",
        name="spot_quote",
        value=values,
        unit=f"{context.quote_currency}_per_{context.base_currency}",
        observation_time=price_time,
        available_time=price_time,
        source=str(row.get("source") or raw.source_name),
        source_identifier=str(
            row.get("source_identifier") or f"{context.base_currency}="
        ),
        source_table="latest_prices",
        quality_status=quality,
        notes="Excel/Reader snapshot quote quality and freshness check (24h window).",
    )
    warnings = []
    if abnormal:
        warnings.append("quote violates non-zero or bid/mid/ask ordering rules")
    if partial:
        warnings.append("quote has missing bid/ask/mid fields")
    if stale:
        warnings.append(
            f"quote is stale: {price_time.isoformat()} is more than 24h before as_of"
        )
    return (
        [item],
        DomainManifest(
            status="complete" if quality == "fresh" else "partial",
            record_count=1,
            missing_fields=missing,
            warnings=warnings,
        ),
        quality,
    )


def _build_technical(
    context: EvidenceContext,
    raw: RawFxSnapshot,
    quote_quality: Literal["fresh", "stale", "partial", "abnormal", "missing"],
) -> tuple[TechnicalRegime, list[EvidenceItem], DomainManifest]:
    daily_rows = [
        row for row in raw.bars if str(row.get("frequency") or "").lower() == "daily"
    ]
    hourly_rows = [
        row for row in raw.bars if str(row.get("frequency") or "").lower() == "hourly"
    ]
    daily = normalize_bars(daily_rows, as_of=context.as_of)
    hourly = normalize_bars(hourly_rows, as_of=context.as_of)
    four_hour = _complete_four_hour_bars(hourly, context.as_of)
    inputs = {"1D": daily, "4H": four_hour}
    periods = {"1D": 252, "4H": 1512}
    states: dict[str, TechnicalTimeframeState] = {}
    evidence: list[EvidenceItem] = []
    missing_data: list[str] = []
    for timeframe in ("1D", "4H"):
        bars = inputs[timeframe]
        metrics = technical_metrics(
            bars,
            periods_per_year=periods[timeframe],
            min_bars=TECHNICAL_OBSERVATION_MIN_BARS,
        )
        if not metrics:
            reason = (
                f"{timeframe} requires at least "
                f"{TECHNICAL_OBSERVATION_MIN_BARS} complete bars for observation"
            )
            missing_data.append(reason)
            states[timeframe] = TechnicalTimeframeState(
                timeframe=timeframe,
                state="indeterminate",
                bar_count=len(bars),
                latest_bar_time=bars[-1]["bar_time"] if bars else None,
                reason=reason,
            )
            continue
        family = f"technical:{timeframe}:{bars[-1]['bar_time'].isoformat()}"
        ids: list[str] = []
        metric_values: dict[str, float] = {}
        for name, (value, calculation) in metrics.items():
            evidence_id = _id(
                "technical", context.evidence_context_id, timeframe, name, family
            )
            ids.append(evidence_id)
            metric_values[name] = value
            evidence.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    evidence_context_id=context.evidence_context_id,
                    evidence_family_id=family,
                    domain="technical",
                    name=name,
                    timeframe=timeframe,
                    value=value,
                    unit="ratio" if name.startswith("return") else "numeric",
                    observation_time=bars[-1]["bar_time"],
                    available_time=bars[-1]["bar_time"],
                    source=str(bars[-1].get("source") or raw.source_name),
                    source_identifier=str(
                        bars[-1].get("source_identifier") or f"{context.base_currency}="
                    ),
                    source_table="market_bars",
                    calculation=calculation,
                    quality_status="fresh",
                )
            )
        full_confirmation = (
            len(bars) >= TECHNICAL_CONFIRMATION_MIN_BARS
            and "ema_50" in metric_values
            and "return_20" in metric_values
        )
        reason = None
        if not full_confirmation:
            reason = (
                f"{timeframe} has {len(bars)} complete bars; observation metrics are "
                f"available, but full confirmation requires "
                f"{TECHNICAL_CONFIRMATION_MIN_BARS} bars"
            )
            missing_data.append(reason)
        states[timeframe] = TechnicalTimeframeState(
            timeframe=timeframe,
            state=_technical_state(metric_values) if full_confirmation else "indeterminate",
            bar_count=len(bars),
            latest_bar_time=bars[-1]["bar_time"],
            metrics=metric_values,
            evidence_ids=ids,
            reason=reason,
        )
    status: Status = (
        "complete"
        if all(item.state != "indeterminate" for item in states.values())
        and quote_quality == "fresh"
        else "partial" if raw.bars else "insufficient_evidence"
    )
    all_ids = [
        evidence_id for state in states.values() for evidence_id in state.evidence_ids
    ]
    return (
        TechnicalRegime(
            status=status,
            timeframes=states,
            quote_quality=quote_quality,
            evidence_ids=all_ids,
            missing_data=missing_data,
        ),
        evidence,
        DomainManifest(
            status=status,
            record_count=len(raw.bars),
            missing_fields=raw.missing_columns.get("bars", []),
            warnings=missing_data,
        ),
    )


def _build_macro(
    context: EvidenceContext, raw: RawFxSnapshot
) -> tuple[RelativeMacroScorecard, list[EvidenceItem], DomainManifest]:
    evidence: list[EvidenceItem] = []
    rows: list[tuple[dict[str, Any], str]] = []
    missing_forecasts = 0
    for row in sorted(
        raw.macro, key=lambda item: as_utc_datetime(item["release_time"])
    ):
        release_time = as_utc_datetime(row["release_time"])
        metric_id = str(row.get("metric_id") or "UNKNOWN").upper()
        country = str(row.get("country") or "").upper()
        source_identifier = str(row.get("source_identifier") or "")
        # The same metric is legitimately published for both legs of a pair
        # at the same timestamp (for example EU and US CPI). Country and the
        # provider identifier are therefore part of the evidence identity;
        # metric + release time alone would collide and make the frozen store
        # reject the second observation as conflicting content.
        family = f"macro:{metric_id}:{country}:{release_time.isoformat()}"
        evidence_id = _id(
            "macro",
            context.evidence_context_id,
            family,
            source_identifier,
        )
        forecast = _float(row.get("forecast_value"))
        if forecast is None:
            missing_forecasts += 1
        evidence.append(
            EvidenceItem(
                evidence_id=evidence_id,
                evidence_context_id=context.evidence_context_id,
                evidence_family_id=family,
                domain="macro",
                name=metric_id,
                value={
                    "actual": _float(row.get("value")),
                    "previous": _float(row.get("previous_value")),
                    "forecast": forecast,
                    "revised": _float(row.get("revised_value")),
                    "country": country,
                },
                unit=str(row.get("unit") or "unknown"),
                observation_time=release_time,
                available_time=release_time,
                source=str(row.get("source") or raw.source_name),
                source_identifier=source_identifier or metric_id,
                source_table="macro_observations",
                quality_status="partial" if forecast is None else "fresh",
                notes=(
                    "forecast missing; surprise unavailable"
                    if forecast is None
                    else None
                ),
            )
        )
        rows.append((row, evidence_id))

    base_country, quote_country = _macro_countries(context)
    signals = _macro_signals(rows, base_country, quote_country)
    usable = [signal for signal in signals if signal.relationship != "unknown"]
    status: Status = (
        "complete"
        if usable and missing_forecasts < len(rows)
        else "partial" if rows else "insufficient_evidence"
    )
    missing_data = []
    if missing_forecasts:
        missing_data.append(
            f"forecast unavailable for {missing_forecasts}/{len(rows)} macro rows"
        )
    if not usable:
        missing_data.append(
            f"no comparable {base_country}/{quote_country} macro signal"
        )
    ids = [item.evidence_id for item in evidence]
    scorecard = RelativeMacroScorecard(
        status=status,
        signals=signals,
        evidence_ids=ids,
        missing_data=missing_data,
    )
    return (
        scorecard,
        evidence,
        DomainManifest(
            status=status,
            record_count=len(rows),
            missing_fields=raw.missing_columns.get("macro", []),
            warnings=missing_data,
        ),
    )


def _macro_signals(
    rows: list[tuple[dict[str, Any], str]],
    base_country: str,
    quote_country: str,
) -> list[MacroSignal]:
    groups = {
        "rates": (
            "POLICY_RATE",
            "INTEREST_RATE",
            "FED_FUNDS",
            "REFI_RATE",
            "BOND_YIELD",
        ),
        "growth": ("GDP", "PMI", "RETAIL", "INDUSTRIAL", "NFP"),
        "labor": ("UNEMPLOYMENT",),
        "inflation": ("CPI", "PCE"),
    }
    signals: list[MacroSignal] = []
    for dimension, tokens in groups.items():
        selected = [
            (row, eid)
            for row, eid in rows
            if any(token in str(row.get("metric_id") or "").upper() for token in tokens)
        ]
        latest: dict[str, tuple[dict[str, Any], str]] = {}
        for row, eid in selected:
            country = str(row.get("country") or "").upper()
            if country in {base_country, quote_country}:
                latest[country] = (row, eid)
        if not {base_country, quote_country}.issubset(latest):
            signals.append(
                MacroSignal(
                    dimension=dimension,
                    relationship="unknown",
                    explanation=(
                        f"both {base_country} and {quote_country} observations are required"
                    ),
                )
            )
            continue
        base_row, base_id = latest[base_country]
        quote_row, quote_id = latest[quote_country]
        if dimension == "rates":
            base_value = _float(base_row.get("value"))
            quote_value = _float(quote_row.get("value"))
        else:
            base_value = _surprise(base_row)
            quote_value = _surprise(quote_row)
        if base_value is None or quote_value is None:
            relationship = "unknown"
            difference = None
        else:
            if dimension == "labor":
                base_value, quote_value = -base_value, -quote_value
            difference = base_value - quote_value
            if dimension == "inflation":
                relationship = "conditional"
            elif math.isclose(difference, 0.0, abs_tol=1e-12):
                relationship = "balanced"
            else:
                relationship = "base_supported" if difference > 0 else "quote_supported"
        signals.append(
            MacroSignal(
                dimension=dimension,
                relationship=relationship,
                base_value=base_value,
                quote_value=quote_value,
                difference=difference,
                evidence_ids=[base_id, quote_id],
                explanation=(
                    f"{base_country} minus {quote_country} level"
                    if dimension == "rates"
                    else (
                        "inflation surprise requires policy/regime interpretation"
                        if dimension == "inflation"
                        else (
                            f"{base_country} minus {quote_country} "
                            "actual-versus-forecast surprise"
                        )
                    )
                ),
            )
        )
    return signals


def _build_stories(
    context: EvidenceContext, raw: RawFxSnapshot
) -> tuple[list[StoryCluster], list[EvidenceItem], DomainManifest]:
    articles: list[dict[str, Any]] = []
    for row in raw.news:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        articles.append(
            {
                **row,
                "publish_time": as_utc_datetime(row["publish_time"]),
                "normalized_title": _normalize_title(title),
                "tags": _news_tags(row),
            }
        )
    articles.sort(key=lambda item: item["publish_time"])
    grouped: list[list[dict[str, Any]]] = []
    for article in articles:
        match = next(
            (
                cluster
                for cluster in grouped
                if article["publish_time"] - cluster[-1]["publish_time"]
                <= timedelta(hours=48)
                and set(article["tags"]) & set(cluster[0]["tags"])
                and SequenceMatcher(
                    None, article["normalized_title"], cluster[0]["normalized_title"]
                ).ratio()
                >= 0.88
            ),
            None,
        )
        if match is None:
            grouped.append([article])
        else:
            match.append(article)

    stories: list[StoryCluster] = []
    evidence: list[EvidenceItem] = []
    for cluster in grouped:
        representative = cluster[0]
        article_ids = [
            str(row.get("article_id") or row.get("id") or "") for row in cluster
        ]
        story_id = _id(
            "story",
            representative["normalized_title"],
            representative["publish_time"].date().isoformat(),
        )
        evidence_id = _id("news", context.evidence_context_id, story_id)
        tags = sorted({tag for row in cluster for tag in row["tags"]})
        language_quality = _language_quality(representative)
        stories.append(
            StoryCluster(
                story_id=story_id,
                representative_title=str(representative["title"]),
                publish_time=representative["publish_time"],
                article_ids=article_ids,
                tags=tags,
                language_quality=language_quality,
                evidence_ids=[evidence_id],
            )
        )
        evidence.append(
            EvidenceItem(
                evidence_id=evidence_id,
                evidence_context_id=context.evidence_context_id,
                evidence_family_id=f"story:{story_id}",
                domain="news",
                name="news_story_cluster",
                value={
                    "title": representative["title"],
                    "article_count": len(cluster),
                    "tags": tags,
                    "language_quality": language_quality,
                },
                unit="story",
                observation_time=representative["publish_time"],
                available_time=representative["publish_time"],
                source=str(representative.get("source") or raw.source_name),
                source_identifier=story_id,
                source_table="news_articles",
                source_record_ids=article_ids,
                quality_status="partial" if language_quality == "suspect" else "fresh",
                notes="Deterministic title/time/tag cluster; article body excluded.",
            )
        )
    status: Status = "complete" if stories else "insufficient_evidence"
    return (
        stories,
        evidence,
        DomainManifest(
            status=status,
            record_count=len(stories),
            missing_fields=raw.missing_columns.get("news", []),
            warnings=(
                [] if stories else [f"no {context.display_symbol}-related news stories"]
            ),
        ),
    )


def _complete_four_hour_bars(
    hourly: list[dict[str, Any]], as_of: datetime
) -> list[dict[str, Any]]:
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    for row in hourly:
        timestamp = row["bar_time"]
        start = timestamp.replace(
            hour=(timestamp.hour // 4) * 4, minute=0, second=0, microsecond=0
        )
        buckets.setdefault(start, []).append(row)
    result: list[dict[str, Any]] = []
    for start, rows in sorted(buckets.items()):
        unique_hours = {
            row["bar_time"].replace(minute=0, second=0, microsecond=0) for row in rows
        }
        expected_hours = {start + timedelta(hours=offset) for offset in range(4)}
        if (
            len(rows) != 4
            or unique_hours != expected_hours
            or start + timedelta(hours=4) > as_of
        ):
            continue
        ordered = sorted(rows, key=lambda item: item["bar_time"])
        result.append(
            {
                **ordered[-1],
                "bar_time": start + timedelta(hours=4),
                "frequency": "4H",
                "open": ordered[0]["open"],
                "high": max(item["high"] for item in ordered),
                "low": min(item["low"] for item in ordered),
                "close": ordered[-1]["close"],
                "volume": sum(item["volume"] for item in ordered),
            }
        )
    return result


def _quote_quality(row: dict[str, Any], as_of: datetime | None) -> str:
    """Return the same freshness label used by canonical quote evidence."""
    if as_of is None:
        return "unknown"
    try:
        price_time = as_utc_datetime(row.get("price_time"))
    except (TypeError, ValueError):
        return "unknown"
    return "stale" if as_of - price_time > _QUOTE_MAX_AGE else "fresh"


def _technical_state(
    metrics: dict[str, float],
) -> Literal["bullish", "bearish", "range", "transition"]:
    close = metrics["latest_close"]
    ema20 = metrics["ema_20"]
    ema50 = metrics["ema_50"]
    return20 = metrics["return_20"]
    if close > ema20 > ema50 and return20 > 0:
        return "bullish"
    if close < ema20 < ema50 and return20 < 0:
        return "bearish"
    if abs(return20) < 0.005:
        return "range"
    return "transition"


def _surprise(row: dict[str, Any]) -> float | None:
    actual = _float(row.get("value"))
    forecast = _float(row.get("forecast_value"))
    return None if actual is None or forecast is None else actual - forecast


def _news_tags(row: dict[str, Any]) -> list[str]:
    tags: set[str] = set()
    for field in ("related_entities", "keywords"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = {field: value}
        if isinstance(value, dict):
            for child in value.values():
                if isinstance(child, list):
                    tags.update(str(item).upper() for item in child)
                elif child:
                    tags.add(str(child).upper())
    return sorted(tags & {"EU", "US", "FX", "RISK"}) or ["FX"]


def _normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", title).lower()
    value = re.sub(r"\b(?:update|corrected|breaking)\s*\d*\b", " ", value)
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def _language_quality(row: dict[str, Any]) -> Literal["declared", "suspect", "unknown"]:
    language = str(row.get("language") or "").lower()
    title = str(row.get("title") or "")
    if not language:
        return "unknown"
    non_ascii = sum(ord(char) > 127 for char in title)
    if language == "en" and title and non_ascii / len(title) > 0.2:
        return "suspect"
    return "declared"


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _id(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"fxe-{hashlib.sha256(payload).hexdigest()[:20]}"
