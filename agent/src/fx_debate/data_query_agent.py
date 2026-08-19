"""MCP-backed FX data query agent.

The data provider remains an independent process. The production path uses a
local MCP stdio subprocess; the old HTTP client remains available only for
backward-compatible callers and tests. This module owns the integration
contract: natural-language query planning, bounded requests, structured
errors, and a small callback seam for Session/SSE observability.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

from fastmcp.client import Client
from fastmcp.client.transports.stdio import StdioTransport

from src.tools.mcp import _run_sync
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


class DataSearchClient(Protocol):
    """统一描述 HTTP 兼容客户端和 MCP 客户端的最小调用接口。"""

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
        """执行一次受控自然语言数据查询。"""
        ...


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


class McpAiSearchClient:
    """通过本地 MCP stdio 子进程调用 AI Search 的统一工具。

    每次查询创建一个短生命周期 MCP 会话，避免数据库连接、模型请求或
    子进程状态跨请求泄漏。AI Search 的环境变量会从其项目目录加载，主
    Agent 只传递业务查询参数，不传递数据库密码或模型密钥。
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        *,
        working_directory: str,
        timeout_seconds: float = 30.0,
        max_rows: int = 250,
        env: Mapping[str, str] | None = None,
        trace_callback: TraceCallback | None = None,
    ) -> None:
        self.command = command
        self.args = list(args)
        self.working_directory = working_directory
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.env = dict(env or {})
        self._trace_callback = trace_callback

    @classmethod
    def from_repository(
        cls,
        *,
        command: str = "",
        args_json: str = "",
        server_module: str = "backend.mcp_server",
        working_directory: str = "",
        timeout_seconds: float = 30.0,
        max_rows: int = 250,
        trace_callback: TraceCallback | None = None,
    ) -> "McpAiSearchClient":
        """根据当前仓库位置创建默认 AI Search MCP 客户端。"""

        repository_root = Path(__file__).resolve().parents[3]
        default_directory = repository_root / "external" / "market-data-tools"
        directory = Path(working_directory).expanduser() if working_directory else default_directory
        return cls(
            command=command or sys.executable,
            args=_parse_mcp_args(args_json, server_module),
            working_directory=str(directory),
            timeout_seconds=timeout_seconds,
            max_rows=max_rows,
            trace_callback=trace_callback,
        )

    @property
    def is_configured(self) -> bool:
        """返回 MCP 命令和工作目录是否可启动。"""

        return bool(self.command and Path(self.working_directory).is_dir())

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
        """调用 MCP 的唯一工具 unified_search。"""

        if tool != "unified_search":
            raise FxDataServiceError(
                f"MCP AI Search 只支持 unified_search，收到：{tool}",
                code="INVALID_DATA_TOOL",
            )
        clean_query = str(query or "").strip()
        if not clean_query:
            raise FxDataServiceError("数据查询问题不能为空", code="INVALID_DATA_QUERY")
        row_limit = max_rows if max_rows is not None else self.max_rows
        if not 1 <= row_limit <= 1000:
            raise FxDataServiceError("max_rows 必须在 1 到 1000 之间", code="INVALID_DATA_QUERY")
        payload: dict[str, Any] = {"query": clean_query, "max_rows": row_limit}
        if provider:
            payload["provider"] = provider
        if start_date is not None:
            payload["start_date"] = _iso_date(start_date)
        if end_date is not None:
            payload["end_date"] = _iso_date(end_date)

        started = time.perf_counter()
        self._trace(
            "data_service.query_started",
            {"transport": "mcp_stdio", "tool": tool, "input": payload},
        )
        try:
            result = _run_sync(lambda: self._call_mcp(payload))
            if not isinstance(result, dict):
                raise FxDataServiceError(
                    "MCP 数据服务返回不是 JSON 对象", code="INVALID_DATA_RESPONSE"
                )
            if str(result.get("status") or "") != "success":
                error = result.get("error")
                error_message = error.get("message") if isinstance(error, dict) else error
                raise FxDataServiceError(
                    str(result.get("message") or error_message or "数据服务拒绝了查询"),
                    code=str(result.get("code") or (error.get("code") if isinstance(error, dict) else "DATA_QUERY_REJECTED")),
                )
            self._trace(
                "data_service.query_completed",
                {
                    "transport": "mcp_stdio",
                    "tool": tool,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "output": _summary(result),
                },
            )
            return result
        except FxDataServiceError as exc:
            self._trace(
                "data_service.query_failed",
                {"transport": "mcp_stdio", "tool": tool, "code": exc.code, "error": str(exc)},
            )
            raise
        except (TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
            wrapped = FxDataServiceError(
                f"MCP 数据服务连接失败：{exc}", code="FX_DATA_SERVICE_UNAVAILABLE"
            )
            self._trace(
                "data_service.query_failed",
                {"transport": "mcp_stdio", "tool": tool, "code": wrapped.code, "error": str(wrapped)},
            )
            raise wrapped from exc

    async def _call_mcp(self, payload: dict[str, Any]) -> dict[str, Any]:
        """启动 stdio 服务、调用工具并提取结构化返回值。"""

        child_env = os.environ.copy()
        child_env.update(self.env)
        transport = StdioTransport(
            command=self.command,
            args=self.args,
            env=child_env,
            cwd=self.working_directory,
            keep_alive=False,
        )
        async with Client(
            transport,
            name="fx-debate-ai-search",
            timeout=self.timeout_seconds,
            init_timeout=max(self.timeout_seconds, 30.0),
        ) as client:
            result = await client.call_tool(
                "unified_search",
                arguments=payload,
                timeout=self.timeout_seconds,
                raise_on_error=False,
            )
        return _extract_mcp_payload(result)

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._trace_callback is not None:
            self._trace_callback({"type": event_type, **payload})


class FxDataQueryAgent:
    """Plan and execute data queries for direct requests and Debate runs.

    This is an integration-layer specialist, not a sixth Debate member. The
    five Debate roles consume its frozen snapshot through the existing bundle
    tools, while the top-level Agent can call ``query_fx_data`` directly.
    """

    def __init__(self, client: DataSearchClient) -> None:
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
        "price": "unified_search",
        "prices": "unified_search",
        "latest_prices": "unified_search",
        "bars": "unified_search",
        "market_bars": "unified_search",
        "macro": "unified_search",
        "macro_observations": "unified_search",
        "news": "unified_search",
        "news_articles": "unified_search",
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


def _parse_mcp_args(raw_args: str, server_module: str) -> list[str]:
    """解析 MCP 参数配置；默认启动 backend.mcp_server 模块。"""

    if not raw_args.strip():
        return ["-m", server_module]
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError:
        parsed = shlex.split(raw_args, posix=False)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("FX_DATA_MCP_ARGS 必须是 JSON 字符串数组")
    return parsed


def _extract_mcp_payload(result: Any) -> dict[str, Any]:
    """兼容 FastMCP 不同版本的 CallToolResult 结构。"""

    if getattr(result, "is_error", False):
        raise FxDataServiceError(
            _mcp_content_text(result) or "MCP 工具调用失败",
            code="FX_DATA_SERVICE_UNAVAILABLE",
        )
    for candidate in (
        getattr(result, "data", None),
        getattr(result, "structured_content", None),
    ):
        if isinstance(candidate, dict):
            if isinstance(candidate.get("result"), dict):
                return candidate["result"]
            if "status" in candidate:
                return candidate
    text = _mcp_content_text(result)
    if text:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    raise FxDataServiceError(
        "MCP 工具返回中没有结构化查询结果", code="INVALID_DATA_RESPONSE"
    )


def _mcp_content_text(result: Any) -> str:
    """提取 MCP 文本内容，便于保留服务端错误信息。"""

    texts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts).strip()


__all__ = [
    "AiSearchClient",
    "DataSearchClient",
    "DataQueryPlan",
    "FxDataQueryAgent",
    "FxDataServiceError",
    "McpAiSearchClient",
]
