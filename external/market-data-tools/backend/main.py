"""可移植 AI Search 的 FastAPI 服务入口。

正式工具接口只返回 status + data；调试用 SSE 接口额外返回查询阶段，供 tools/front
测试工作台观察输入、输出、耗时和停止原因。所有密钥和数据库连接都留在服务端。
"""

from __future__ import annotations

import asyncio
from datetime import date
import json
from queue import Queue
from threading import Thread
from typing import Any, Iterator

import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .ai_search.config import configuration_status, database_connection_kwargs
from .ai_search.public_response import (
    build_evidence_response,
    build_public_error,
    build_public_response,
)
from .ai_search.tool_registry import (
    get_tool_definitions,
    run_tool_internal,
)


class ToolRequest(BaseModel):
    """五个工具共用的受控请求模型；不允许传入 route、表名、字段名或 SQL。"""

    query: str = Field(min_length=1, max_length=1000)
    provider: str | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    max_rows: int = Field(default=100, ge=1, le=1000)

    # Agent 输入必须是受控业务参数，意外传入 route、表名或 SQL 时直接拒绝。
    model_config = ConfigDict(extra="forbid")


class InstrumentSearchRequest(BaseModel):
    """标准金融工具路由的受控请求，不接受 provider、表名或 SQL。"""

    query: str = Field(min_length=1, max_length=1000)
    max_rows: int = Field(default=1, ge=1, le=10)
    model_config = ConfigDict(extra="forbid")


def _arguments(request: ToolRequest) -> dict[str, Any]:
    """兼容 Pydantic v1/v2，把请求转换为工具函数参数。"""

    if hasattr(request, "model_dump"):
        return request.model_dump()
    return request.dict()


def _public_result(name: str, request: ToolRequest) -> dict[str, Any]:
    """执行正式工具接口并隐藏内部候选和目录结果。"""

    try:
        return build_public_response(
            run_tool_internal(name, **_arguments(request))
        )
    except Exception as exc:  # noqa: BLE001 - API 需要稳定错误协议
        return build_public_error(type(exc).__name__, str(exc))


def _sse_message(event_type: str, payload: Any) -> str:
    """将阶段事件编码为浏览器可消费的 SSE 文本。"""

    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _event_iterator(name: str, request: ToolRequest) -> Iterator[str]:
    """在线程中执行同步数据库/模型调用，并逐阶段输出事件。"""

    events: Queue[tuple[str, Any]] = Queue()

    def publish(stage: dict[str, Any]) -> None:
        """接收内部编排器事件，放入当前请求专属队列。"""

        events.put(("stage", stage))

    def worker() -> None:
        """隔离阻塞式数据库和模型调用，避免阻塞 FastAPI 事件循环。"""

        try:
            result = run_tool_internal(name, trace_callback=publish, **_arguments(request))
            events.put(("result", result))
        except Exception as exc:  # noqa: BLE001 - 前端需要看到明确错误
            events.put(("error", {"message": str(exc), "error_type": type(exc).__name__}))
        finally:
            events.put(("done", {"finished_at": date.today().isoformat()}))

    Thread(target=worker, daemon=True, name="portable-ai-search-query").start()
    while True:
        event_type, payload = events.get()
        yield _sse_message(event_type, payload)
        if event_type == "done":
            break


async def _stream(name: str, request: ToolRequest) -> Any:
    """异步包装同步事件迭代器。"""

    iterator = _event_iterator(name, request)
    while True:
        message = await asyncio.to_thread(next, iterator, None)
        if message is None:
            break
        yield message
        if "event: done\n" in message:
            break


app = FastAPI(title="ICBC Trading Portable AI Search", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    """检查配置和数据库连通性，但不返回密码或 API Key。"""

    status = configuration_status()
    database = "error"
    database_error = None
    if status["database_configured"]:
        try:
            with psycopg2.connect(**database_connection_kwargs()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            database = "ok"
        except Exception as exc:  # noqa: BLE001 - 健康检查需要记录根因
            database_error = str(exc)
    else:
        database_error = "未配置数据库密码"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "database_error": database_error,
        **status,
    }


@app.get("/tools/definitions")
def definitions() -> list[dict[str, Any]]:
    """返回 Agent 注册五个工具所需的函数定义。"""

    return get_tool_definitions()


def _endpoint(name: str):
    """创建精简工具接口，减少五个入口之间的重复实现。"""

    def handler(request: ToolRequest) -> dict[str, Any]:
        return _public_result(name, request)

    return handler


def _instrument_endpoint(request: InstrumentSearchRequest) -> dict[str, Any]:
    """执行独立 instrument_search HTTP 路由，并保持 status + data 协议。"""

    try:
        arguments = _arguments(request)
        return build_public_response(run_tool_internal("instrument_search", **arguments))
    except Exception as exc:  # noqa: BLE001 - 路由需要稳定的结构化错误
        return build_public_error(type(exc).__name__, str(exc))


app.post("/tools/unified_search")(_endpoint("unified_search"))
app.post("/tools/latest_prices_search")(_endpoint("latest_prices_search"))
app.post("/tools/market_bars_search")(_endpoint("market_bars_search"))
app.post("/tools/macro_observations_search")(_endpoint("macro_observations_search"))
app.post("/tools/news_articles_search")(_endpoint("news_articles_search"))
app.post("/tools/instrument_search")(_instrument_endpoint)


_EVIDENCE_TOOLS = {
    "latest_prices_search",
    "market_bars_search",
    "macro_observations_search",
    "news_articles_search",
    "unified_search",
}


@app.post("/v1/evidence/{name}")
def evidence_endpoint(name: str, request: ToolRequest) -> dict[str, Any]:
    """Return a stable, provenance-preserving response for FX Debate.

    The existing ``/tools/*`` endpoints remain unchanged. This endpoint is a
    read-only integration contract and only accepts the same allowlisted tool
    names; it never accepts a table, field, route, or SQL fragment.
    """
    if name not in _EVIDENCE_TOOLS:
        raise HTTPException(status_code=404, detail="unknown evidence tool")
    try:
        return build_evidence_response(
            run_tool_internal(name, **_arguments(request))
        )
    except Exception as exc:  # noqa: BLE001 - preserve structured service errors
        return build_public_error(type(exc).__name__, str(exc)) | {
            "schema_version": "fx-evidence.v1"
        }


def _stream_endpoint(name: str):
    """创建调试 SSE 接口；它不改变正式工具的精简响应协议。"""

    async def handler(request: ToolRequest) -> StreamingResponse:
        return StreamingResponse(
            _stream(name, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return handler


app.post("/tools/unified_search/stream")(_stream_endpoint("unified_search"))
app.post("/tools/latest_prices_search/stream")(_stream_endpoint("latest_prices_search"))
app.post("/tools/market_bars_search/stream")(_stream_endpoint("market_bars_search"))
app.post("/tools/macro_observations_search/stream")(_stream_endpoint("macro_observations_search"))
app.post("/tools/news_articles_search/stream")(_stream_endpoint("news_articles_search"))
