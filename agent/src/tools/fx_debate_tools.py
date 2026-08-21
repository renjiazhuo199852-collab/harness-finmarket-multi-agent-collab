"""Run-scoped FX Debate evidence Tools built above the internal Reader SDK."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from src.agent.tools import BaseTool
from src.fx_debate.analytics import (
    aggregate_four_hour,
    as_utc_datetime,
    normalize_bars,
    technical_metrics,
)
from src.fx_debate.models import (
    EvidenceContext,
    EvidenceError,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.fx_debate.runtime_context import resolve_runtime_resources
from src.fx_debate.store import FxEvidenceStore
from src.market_data_reader import MarketDataReader, MarketDataReaderError
from src.market_database import MarketDatabaseUnavailable

_AVAILABLE_TIME_WARNING = (
    "当前 SDK 未提供独立 available_time；本次以 bar_time 近似，"
    "Evidence Item 已保留该限制。"
)


class _FxContextTool(BaseTool):
    """Resolve and enforce a single immutable Evidence Context."""

    def __init__(
        self,
        *,
        context: EvidenceContext | None = None,
        store: FxEvidenceStore | None = None,
    ) -> None:
        self._context = context
        self._store = store

    def _resources(
        self, kwargs: dict[str, Any]
    ) -> tuple[EvidenceContext, FxEvidenceStore]:
        if self._context is not None and self._store is not None:
            context, store = self._context, self._store
        else:
            context, store = resolve_runtime_resources(kwargs.get("run_dir"))
        requested_id = str(kwargs.get("evidence_context_id") or "")
        if requested_id != context.evidence_context_id:
            raise ValueError("Evidence Context ID does not match the owning Swarm run")
        return context, store

    @classmethod
    def check_available(cls) -> bool:
        """Expose context Tools when the configured FX data source is ready."""
        from src.config.accessor import get_env_config

        config = get_env_config()
        if config.fx_debate.data_source == "excel":
            return bool(
                config.fx_debate.excel_path
                and Path(config.fx_debate.excel_path).expanduser().is_file()
            )
        return MarketDataReader().is_configured

    @staticmethod
    def _error(context_id: Any, message: str) -> str:
        result = EvidenceQueryResult(
            ok=False,
            status="error",
            evidence_context_id=str(context_id or ""),
            query_id="",
            errors=[
                EvidenceError(
                    code="FX_EVIDENCE_ERROR",
                    message=message,
                )
            ],
        )
        return result.model_dump_json()


class _FxMarketDataTool(_FxContextTool):
    """Shared database availability and Reader injection."""

    def __init__(
        self,
        *,
        reader: MarketDataReader | Any | None = None,
        context: EvidenceContext | None = None,
        store: FxEvidenceStore | None = None,
    ) -> None:
        super().__init__(context=context, store=store)
        self._reader = reader or MarketDataReader()

    @classmethod
    def check_available(cls) -> bool:
        """Expose domain data Tools only when the internal DB is configured."""
        return MarketDataReader().is_configured


class GetFxMarketEvidenceTool(_FxMarketDataTool):
    """Return point-in-time market and technical evidence for one FX pair."""

    name = "get_fx_market_evidence"
    description = (
        "读取当前 FX Debate 固定 Evidence Context 下的 PostgreSQL 行情，"
        "生成 4H/1D 技术指标与可引用 evidence_id；拒绝跨 Context 和未来数据。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "evidence_context_id": {
                "type": "string",
                "description": "本次 Debate 提示词中给出的 Evidence Context ID。",
            }
        },
        "required": ["evidence_context_id"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        """Query existing SDK methods and register deterministic Evidence Items."""
        try:
            context, store = self._resources(kwargs)
            query_id = _market_query_id(context)
            result = store.get_or_create_query(
                query_id,
                lambda: self._query(context),
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
                f"FX 行情证据查询失败：{exc}",
            )

    def _query(self, context: EvidenceContext) -> EvidenceQueryResult:
        provider = context.provider_priority[0]
        common = {
            "symbol": context.canonical_symbol,
            "source": provider,
            "start_date": context.market_start_time.date().isoformat(),
            "end_date": context.as_of.date().isoformat(),
        }
        latest_payload = self._reader.get_latest_prices(
            symbol=context.canonical_symbol,
            source=provider,
        )
        daily_payload = self._reader.get_market_bars(
            **common,
            frequency="daily",
            limit=context.market_bar_limit_per_timeframe["1D"],
        )
        hourly_payload = self._reader.get_market_bars(
            **common,
            frequency="hourly",
            limit=1000,
        )
        daily = normalize_bars(daily_payload.get("bars", []), as_of=context.as_of)
        hourly = normalize_bars(hourly_payload.get("bars", []), as_of=context.as_of)
        timeframe_bars = {
            "1D": daily[-context.market_bar_limit_per_timeframe["1D"] :],
            "4H": aggregate_four_hour(hourly)[
                -context.market_bar_limit_per_timeframe["4H"] :
            ],
        }

        evidence = _latest_price_items(context, latest_payload, provider)
        missing: list[str] = []
        for timeframe in context.timeframes:
            bars = timeframe_bars[timeframe]
            metrics = technical_metrics(
                bars,
                periods_per_year=252 if timeframe == "1D" else 1512,
            )
            if not metrics:
                missing.append(f"{timeframe} 至少需要 50 根有效 K 线")
                continue
            evidence.extend(
                _market_items(
                    context=context,
                    timeframe=timeframe,
                    bars=bars,
                    metrics=metrics,
                    provider=provider,
                )
            )

        status: Literal["complete", "partial", "insufficient_evidence", "error"]
        if evidence and not missing:
            status = "complete"
        elif evidence:
            status = "partial"
        else:
            status = "insufficient_evidence"
        query_id = _market_query_id(context)
        return EvidenceQueryResult(
            ok=True,
            status=status,
            evidence_context_id=context.evidence_context_id,
            query_id=query_id,
            data_as_of=max(
                (item.observation_time for item in evidence),
                default=None,
            ),
            evidence=evidence,
            warnings=[_AVAILABLE_TIME_WARNING],
            missing_data=missing,
        )


class GetFxEvidenceByIdsTool(_FxContextTool):
    """Retrieve registered evidence without requerying PostgreSQL."""

    name = "get_fx_evidence_by_ids"
    description = (
        "按 evidence_id 回查本次 FX Debate 已注册证据的时间、值、单位、质量和 evidence family，"
        "保持输入顺序；用于成稿前核验已选证据，只能读取当前 Evidence Context。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "evidence_context_id": {"type": "string"},
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 100,
            },
        },
        "required": ["evidence_context_id", "evidence_ids"],
    }
    repeatable = True

    @classmethod
    def check_available(cls) -> bool:
        """Evidence lookup uses the run-scoped store, not the market DB.

        The MCP source populates this store before the Swarm starts, so this
        tool must remain available even when PostgreSQL is intentionally off.
        """
        return True

    def execute(self, **kwargs: Any) -> str:
        """Read Evidence Items by ID from the run-scoped store."""
        try:
            if self._context is None or self._store is None:
                from src.fx_debate.runtime_context import resolve_runtime_bundle

                context, _, store = resolve_runtime_bundle(kwargs.get("run_dir"))
                if (
                    str(kwargs.get("evidence_context_id") or "")
                    != context.evidence_context_id
                ):
                    raise ValueError(
                        "Evidence Context ID does not match the owning Swarm run"
                    )
            else:
                context, store = self._resources(kwargs)
            evidence_ids = kwargs.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ValueError("evidence_ids must be a non-empty list")
            evidence, missing = store.get([str(item) for item in evidence_ids])
            return json.dumps(
                {
                    "ok": True,
                    "status": "complete" if not missing else "partial",
                    "evidence_context_id": context.evidence_context_id,
                    "evidence": [item.model_dump(mode="json") for item in evidence],
                    "not_found_ids": missing,
                },
                ensure_ascii=False,
            )
        except ValueError as exc:
            return self._error(kwargs.get("evidence_context_id"), str(exc))


def _market_items(
    *,
    context: EvidenceContext,
    timeframe: Literal["4H", "1D"],
    bars: list[dict[str, Any]],
    metrics: dict[str, tuple[float, str | None]],
    provider: str,
) -> list[EvidenceItem]:
    last = bars[-1]
    observation_time: datetime = last["bar_time"]
    source_identifier = last.get("source_identifier")
    price_metrics = {"latest_close", "ema_20", "ema_50", "atr_14", "high_20", "low_20"}
    items: list[EvidenceItem] = []
    for name, (value, calculation) in metrics.items():
        if name in price_metrics:
            unit = f"{context.quote_currency}_per_{context.base_currency}"
        elif name == "rsi_14":
            unit = "index"
        elif name == "realized_vol_20":
            unit = "annualized_ratio"
        else:
            unit = "ratio"
        evidence_id = _stable_id(
            "fxe",
            context.evidence_context_id,
            "technical",
            timeframe,
            name,
            observation_time.isoformat(),
        )
        items.append(
            EvidenceItem(
                evidence_id=evidence_id,
                evidence_context_id=context.evidence_context_id,
                domain="market" if name == "latest_close" else "technical",
                name=name,
                timeframe=timeframe,
                value=value,
                unit=unit,
                observation_time=observation_time,
                available_time=observation_time,
                source=str(last.get("source") or provider),
                source_identifier=(
                    str(source_identifier) if source_identifier is not None else None
                ),
                source_table="public.market_bars",
                calculation=calculation,
                quality_status="fresh",
                notes="available_time 由 bar_time 近似；当前 SDK 未返回记录主键。",
            )
        )
    return items


def _latest_price_items(
    context: EvidenceContext,
    payload: dict[str, Any],
    provider: str,
) -> list[EvidenceItem]:
    """Convert only point-in-time-safe latest-price snapshots to evidence."""
    items: list[EvidenceItem] = []
    for row in payload.get("prices", []):
        price_time = as_utc_datetime(row["price_time"])
        if price_time > context.as_of:
            continue
        values = {
            name: float(row[name]) if row.get(name) is not None else None
            for name in ("last_price", "bid", "ask", "mid_price")
        }
        source_identifier = row.get("source_identifier")
        items.append(
            EvidenceItem(
                evidence_id=_stable_id(
                    "fxe",
                    context.evidence_context_id,
                    "market",
                    "latest_price",
                    price_time.isoformat(),
                    str(row.get("source") or provider),
                ),
                evidence_context_id=context.evidence_context_id,
                domain="market",
                name="latest_price",
                value=values,
                unit=f"{context.quote_currency}_per_{context.base_currency}",
                observation_time=price_time,
                available_time=price_time,
                source=str(row.get("source") or provider),
                source_identifier=(
                    str(source_identifier) if source_identifier is not None else None
                ),
                source_table="public.latest_prices",
                calculation=None,
                quality_status="fresh",
                notes="available_time 由 price_time 近似；当前 SDK 未返回记录主键。",
            )
        )
    return items


def _market_query_id(context: EvidenceContext) -> str:
    return _stable_id(
        "fxq",
        context.evidence_context_id,
        "market",
        ",".join(context.timeframes),
    )


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:20]}"
