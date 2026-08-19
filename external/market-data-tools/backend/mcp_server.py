"""AI Search 的本地 MCP stdio 服务入口。

这个进程只暴露一个 ``unified_search`` 工具。工具内部仍然调用现有的
``tool_registry.unified_search``，因此 MCP 只改变 Agent 与服务之间的通信协议，
不会绕过 dataset_catalog、字段目录、金融工具解析或业务适配器。

stdio 是机器协议通道：任何日志必须写入 stderr，不能写入 stdout，否则会破坏
MCP 的 JSON-RPC 通信。数据库密码、模型密钥和 PostgreSQL 连接仍由本项目自己的
``.env`` 读取，绝不会出现在 MCP 工具参数中。
"""

from __future__ import annotations

import asyncio
import json
import logging
from queue import Empty, Queue
import sys
from datetime import date
from typing import Any

from fastmcp import Context, FastMCP

from .ai_search.public_response import build_public_error, build_public_response
from .ai_search.tool_registry import run_tool_internal


logging.basicConfig(stream=sys.stderr, level=logging.INFO)

mcp = FastMCP("ICBC Trading AI Search")


@mcp.tool(name="unified_search")
async def unified_search(
    query: str,
    provider: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    max_rows: int = 100,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """根据数据集目录自动查询价格、历史行情、宏观指标或新闻。

    调用方只能提供自然语言问题和受控过滤条件。物理表名、字段名、SQL、
    route 和模型控制参数都由服务内部管理，不能从 MCP 输入传入。
    """

    # MCP 的 stdout 只能承载 JSON-RPC 消息，因此不能把阶段日志直接 print
    # 到 stdout。查询线程把完整阶段对象放入线程安全队列，当前协程再通过
    # MCP 标准的 progress notification 转发给调用方，保证既不污染协议，
    # 又能让主 Agent 在查询尚未结束时收到真实的内部执行过程。
    stages: Queue[dict[str, Any]] = Queue()

    def publish_stage(stage: dict[str, Any]) -> None:
        """接收同步查询管道事件，并安全地交给 MCP 协程处理。"""

        stages.put(dict(stage))

    def execute_query() -> dict[str, Any]:
        """在线程中执行阻塞式数据库和模型调用，并保持公开响应协议。"""

        try:
            result = run_tool_internal(
                "unified_search",
                query=query,
                provider=provider,
                start_date=start_date,
                end_date=end_date,
                max_rows=max_rows,
                trace_callback=publish_stage if ctx is not None else None,
            )
            return build_public_response(result)
        except Exception as exc:  # noqa: BLE001 - MCP 必须返回稳定结构化错误
            return build_public_error(type(exc).__name__, str(exc))

    query_task = asyncio.create_task(asyncio.to_thread(execute_query))
    sequence = 0
    while True:
        # 阶段事件由数据库/模型线程产生，按队列顺序发送；不做内容裁剪，
        # 方便前端完整查看本次查询的输入、输出、模型判断和耗时信息。
        while True:
            try:
                stage = stages.get_nowait()
            except Empty:
                break
            sequence += 1
            progress_payload = {
                "type": "mcp_stage",
                "sequence": sequence,
                **stage,
            }
            if ctx is not None:
                try:
                    await ctx.report_progress(
                        progress=float(sequence),
                        total=None,
                        message=json.dumps(
                            progress_payload,
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                except Exception:  # noqa: BLE001 - 流程展示失败不能中断业务查询
                    logging.getLogger(__name__).debug(
                        "MCP progress notification failed",
                        exc_info=True,
                    )

        if query_task.done() and stages.empty():
            break
        # 让事件循环有机会处理 MCP 通知，同时避免忙等占用 CPU。
        await asyncio.sleep(0.01)

    return await query_task


if __name__ == "__main__":
    # 明确指定 stdio，避免本地 FastMCP 默认值变化导致启动方式漂移。
    mcp.run(transport="stdio")
