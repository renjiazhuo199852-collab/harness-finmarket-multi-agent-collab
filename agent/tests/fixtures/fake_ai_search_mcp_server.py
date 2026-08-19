"""用于 FX 数据客户端测试的最小 AI Search MCP 服务。

该服务模拟真实 MCP 的唯一工具 ``unified_search``，不访问数据库，便于测试
主 Agent 的 stdio 启动、参数传递和结构化返回解析。
"""

from __future__ import annotations

import json

from fastmcp import Context, FastMCP

mcp = FastMCP("fake-ai-search")


@mcp.tool(name="unified_search")
async def unified_search(
    query: str,
    ctx: Context,
    provider: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_rows: int = 100,
) -> dict[str, object]:
    """返回调用参数，验证主 Agent 没有绕过统一工具。"""

    # 测试服务模拟真实 AI Search：先发一个完整阶段，再返回业务结果，
    # 用来验证主 Agent 的 MCP progress 接收而不是只验证最终 JSON。
    await ctx.report_progress(
        progress=1,
        total=None,
        message=json.dumps(
            {
                "type": "mcp_stage",
                "sequence": 1,
                "stage": "dataset_catalog",
                "status": "completed",
                "input": {"query": query},
                "output": {"dataset_id": "LSEG_NEWS"},
                "duration_ms": 1.5,
                "error": None,
            },
            ensure_ascii=False,
        ),
    )

    return {
        "status": "success",
        "data": [
            {
                "query": query,
                "provider": provider,
                "start_date": start_date,
                "end_date": end_date,
                "max_rows": max_rows,
            }
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
