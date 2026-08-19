"""AI Search MCP stdio 服务的协议边界测试。"""

from __future__ import annotations

import asyncio
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
