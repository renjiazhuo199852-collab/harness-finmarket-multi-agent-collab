"""AI Search MCP stdio 服务的协议边界测试。"""

from __future__ import annotations

import asyncio
from datetime import date
import sys
from pathlib import Path

from fastmcp.client import Client
from fastmcp.client.transports.stdio import StdioTransport


TOOLS_ROOT = Path(__file__).resolve().parents[1]


def test_stdio_server_exposes_only_unified_search() -> None:
    """MCP 只暴露统一入口，四个独立工具不进入 Agent 的 MCP 面。"""

    async def run() -> list[str]:
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "backend.mcp_server"],
            cwd=str(TOOLS_ROOT),
            keep_alive=False,
        )
        async with Client(transport) as client:
            return [tool.name for tool in await client.list_tools()]

    assert asyncio.run(run()) == ["unified_search"]


def test_unified_search_forwards_complete_stage_through_progress(monkeypatch) -> None:
    """MCP 工具结果保持精简，同时通过 progress 发送完整阶段对象。"""

    from backend import mcp_server

    progress_messages: list[str] = []

    class _Context:
        async def report_progress(self, progress, total=None, message=None):
            progress_messages.append(str(message))

    def fake_run_tool_internal(name, **arguments):
        assert name == "unified_search"
        assert arguments["start_date"] == date(2026, 8, 1)
        arguments["trace_callback"](
            {
                "stage": "dataset_catalog",
                "status": "completed",
                "input": {"query": arguments["query"]},
                "output": {"dataset_id": "LSEG_NEWS"},
                "duration_ms": 12.5,
                "error": None,
            }
        )
        return {
            "adapter": "news_articles",
            "execution": {
                "status": "resolved",
                "adapter": "news_articles",
                "rows": [],
            },
        }

    monkeypatch.setattr(mcp_server, "run_tool_internal", fake_run_tool_internal)
    result = asyncio.run(
        mcp_server.unified_search(
            query="查询 EURUSD 新闻",
            start_date=date(2026, 8, 1),
            ctx=_Context(),
        )
    )

    assert result == {"status": "success", "data": []}
    assert len(progress_messages) == 1
    assert '"type": "mcp_stage"' in progress_messages[0]
    assert '"stage": "dataset_catalog"' in progress_messages[0]
    assert '"dataset_id": "LSEG_NEWS"' in progress_messages[0]
