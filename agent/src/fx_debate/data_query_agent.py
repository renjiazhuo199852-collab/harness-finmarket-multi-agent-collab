"""Service-backed FX data query agent.

The data provider remains an independent HTTP service. This module owns only
the integration contract: natural-language query planning, bounded requests,
structured errors, and a small callback seam for Session/SSE observability.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping
from urllib.parse import quote

from src.fx_debate.models import EvidenceContext

TraceCallback = Callable[[dict[str, Any]], None]
_TOOLS = {
    "unified_search",
    "latest_prices_search",
    "market_bars_search",
    "macro_observations_search",
    "news_articles_search",
}


class FxDataServiceError(RuntimeError):
    """Stable failure raised when the independent data service cannot answer."""

    def __init__(self, message: str, *, code: str = "FX_DATA_SERVICE_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DataQueryPlan:
    """One natural-language request sent to the provider service."""

    domain: str
    tool: str
    query: str
    start_date: str | None = None
    end_date: str | None = None
    max_rows: int = 250


class AiSearchClient:
    """Minimal stdlib HTTP client for the independent AI Search service."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_rows: int = 250,
        opener: Callable[..., Any] | None = None,
        trace_callback: TraceCallback | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self._opener = opener or urllib.request.urlopen
        self._trace_callback = trace_callback

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url.startswith(("http://", "https://")))

    def search(
        self,
        tool: str,
        query: str,
        *,
        provider: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Call one provider evidence endpoint with a natural-language query."""
        if tool not in _TOOLS:
            raise FxDataServiceError(
                f"不支持的数据查询工具：{tool}", code="INVALID_DATA_TOOL"
            )
        clean_query = str(query or "").strip()
        if not clean_query:
            raise FxDataServiceError("数据查询问题不能为空", code="INVALID_DATA_QUERY")
        row_limit = max_rows if max_rows is not None else self.max_rows
        if not 1 <= row_limit <= 1000:
            raise FxDataServiceError("max_rows 必须在 1 到 1000 之间", code="INVALID_DATA_QUERY")
        payload: dict[str, Any] = {
            "query": clean_query,
            "max_rows": row_limit,
        }
        if provider:
            payload["provider"] = provider
        if start_date is not None:
            payload["start_date"] = _iso_date(start_date)
        if end_date is not None:
            payload["end_date"] = _iso_date(end_date)
        url = f"{self.base_url}/v1/evidence/{quote(tool, safe='')}"
        started = time.perf_counter()
        self._trace(
            "data_service.query_started",
            {"tool": tool, "url": url, "input": payload},
        )
        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise FxDataServiceError(
                    "数据服务返回不是 JSON 对象", code="INVALID_DATA_RESPONSE"
                )
            status = str(result.get("status") or "")
            if status != "success":
                raise FxDataServiceError(
                    str(result.get("message") or "数据服务拒绝了查询"),
                    code=str(result.get("code") or "DATA_QUERY_REJECTED"),
                )
            self._trace(
                "data_service.query_completed",
                {
                    "tool": tool,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "output": _summary(result),
                },
            )
            return result
        except FxDataServiceError as exc:
            self._trace(
                "data_service.query_failed",
                {
                    "tool": tool,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "code": exc.code,
                    "error": str(exc),
                },
            )
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            wrapped = FxDataServiceError(
                f"数据服务连接失败：{exc}", code="FX_DATA_SERVICE_UNAVAILABLE"
            )
            self._trace(
                "data_service.query_failed",
                {
                    "tool": tool,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "code": wrapped.code,
                    "error": str(wrapped),
                },
            )
            raise wrapped from exc
        except (json.JSONDecodeError, UnicodeError, TypeError, ValueError) as exc:
            wrapped = FxDataServiceError(
                f"数据服务返回无法解析：{exc}", code="INVALID_DATA_RESPONSE"
            )
            self._trace(
                "data_service.query_failed",
                {
                    "tool": tool,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "code": wrapped.code,
                    "error": str(wrapped),
                },
            )
            raise wrapped from exc

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._trace_callback is not None:
            self._trace_callback({"type": event_type, **payload})


class FxDataQueryAgent:
    """Plan and execute data queries for direct requests and Debate runs.

    This is an integration-layer specialist, not a sixth Debate member. The
    five Debate roles consume its frozen snapshot through the existing bundle
    tools, while the top-level Agent can call ``query_fx_data`` directly.
    """

    def __init__(self, client: AiSearchClient) -> None:
        self.client = client

    def query(
        self,
        query: str,
        *,
        domain: str = "unified",
        provider: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Answer a direct natural-language data request."""
        tool = _tool_for_domain(domain)
        return self.client.search(
            tool,
            query,
            provider=provider,
            start_date=start_date,
            end_date=end_date,
            max_rows=max_rows,
        )

    def plan_for_debate(self, context: EvidenceContext) -> list[DataQueryPlan]:
        """Build deterministic, human-readable queries for one FX snapshot."""
        symbol = context.display_symbol
        start = context.market_start_time.date().isoformat()
        end = context.as_of.date().isoformat()
        end_exclusive = (context.as_of.date() + timedelta(days=1)).isoformat()
        return [
            DataQueryPlan(
                domain="prices",
                tool="unified_search",
                query=f"查询 {symbol} 的最新价格、买价、卖价和中间价",
                max_rows=1,
            ),
            DataQueryPlan(
                domain="bars",
                tool="unified_search",
                query=f"查询 {symbol} 从 {start} 到 {end} 的日线 OHLCV 历史行情",
                start_date=start,
                end_date=end,
            ),
            DataQueryPlan(
                domain="macro",
                tool="unified_search",
                query=f"查询 {symbol} 相关的宏观经济指标观测值、实际值和预测值",
                start_date=start,
                end_date=end_exclusive,
            ),
            DataQueryPlan(
                domain="news",
                tool="unified_search",
                query=f"查询 {symbol} 从 {context.news_start_time.date().isoformat()} 到 {end} 的相关新闻、标题和摘要",
                start_date=context.news_start_time.date().isoformat(),
                end_date=end_exclusive,
            ),
        ]

    def retrieve_for_debate(self, context: EvidenceContext) -> dict[str, dict[str, Any]]:
        """Execute the bounded plan and return domain-keyed service responses."""
        results: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        provider = context.provider_priority[0] if context.provider_priority else None
        for plan in self.plan_for_debate(context):
            try:
                results[plan.domain] = self.client.search(
                    plan.tool,
                    plan.query,
                    provider=provider,
                    start_date=plan.start_date,
                    end_date=plan.end_date,
                    max_rows=plan.max_rows,
                )
            except FxDataServiceError as exc:
                errors.append(f"{plan.domain}: {exc.code}: {exc}")
                results[plan.domain] = {
                    "status": "error",
                    "data": [],
                    "code": exc.code,
                    "message": str(exc),
                }
        if not results or len(errors) == len(results):
            raise FxDataServiceError(
                "数据服务未返回任何可用的 FX 证据：" + "；".join(errors),
                code="FX_DATA_UNAVAILABLE",
            )
        return results


def _tool_for_domain(domain: str) -> str:
    normalized = str(domain or "unified").strip().lower()
    aliases = {
        "unified": "unified_search",
        "all": "unified_search",
        "price": "latest_prices_search",
        "prices": "latest_prices_search",
        "latest_prices": "latest_prices_search",
        "bars": "market_bars_search",
        "market_bars": "market_bars_search",
        "macro": "macro_observations_search",
        "macro_observations": "macro_observations_search",
        "news": "news_articles_search",
        "news_articles": "news_articles_search",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise FxDataServiceError(
            f"不支持的数据领域：{domain}", code="INVALID_DATA_DOMAIN"
        ) from exc


def _iso_date(value: date | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _summary(result: Mapping[str, Any]) -> dict[str, Any]:
    data = result.get("data")
    return {
        "status": result.get("status"),
        "schema_version": result.get("schema_version"),
        "row_count": len(data) if isinstance(data, list) else 0,
        "meta": result.get("meta") if isinstance(result.get("meta"), dict) else {},
    }


__all__ = [
    "AiSearchClient",
    "DataQueryPlan",
    "FxDataQueryAgent",
    "FxDataServiceError",
]
