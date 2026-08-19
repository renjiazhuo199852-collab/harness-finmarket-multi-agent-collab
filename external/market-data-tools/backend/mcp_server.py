"""AI Search 的本地 MCP stdio 服务入口。

这个进程只暴露一个 ``unified_search`` 工具。工具内部仍然调用现有的
``tool_registry.unified_search``，因此 MCP 只改变 Agent 与服务之间的通信协议，
不会绕过 dataset_catalog、字段目录、金融工具解析或业务适配器。

stdio 是机器协议通道：任何日志必须写入 stderr，不能写入 stdout，否则会破坏
MCP 的 JSON-RPC 通信。数据库密码、模型密钥和 PostgreSQL 连接仍由本项目自己的
``.env`` 读取，绝不会出现在 MCP 工具参数中。
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from typing import Any

from fastmcp import FastMCP

from .ai_search.tool_registry import unified_search as _unified_search


logging.basicConfig(stream=sys.stderr, level=logging.INFO)

mcp = FastMCP("ICBC Trading AI Search")


@mcp.tool(name="unified_search")
def unified_search(
    query: str,
    provider: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    max_rows: int = 100,
) -> dict[str, Any]:
    """根据数据集目录自动查询价格、历史行情、宏观指标或新闻。

    调用方只能提供自然语言问题和受控过滤条件。物理表名、字段名、SQL、
    route 和模型控制参数都由服务内部管理，不能从 MCP 输入传入。
    """

    return _unified_search(
        query=query,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        max_rows=max_rows,
    )


if __name__ == "__main__":
    # 明确指定 stdio，避免本地 FastMCP 默认值变化导致启动方式漂移。
    mcp.run(transport="stdio")
