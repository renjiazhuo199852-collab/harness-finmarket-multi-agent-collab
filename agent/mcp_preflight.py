"""Fail-fast MCP stdio readiness check for the FX Debate launcher.

This deliberately performs only the MCP initialize/list-tools handshake. The
MCP server is a short-lived stdio child, so keeping it detached in the
background would not make it usable; real queries are still started by the FX
API with the same transport configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Running this file directly should behave like ``python -m`` from agent/.
AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports.stdio import StdioTransport  # noqa: E402

from src.fx_debate.data_query_agent import (  # noqa: E402
    McpAiSearchClient,
    _build_mcp_child_env,
    _extract_mcp_payload,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the FX Debate MCP stdio handshake")
    parser.add_argument("--python", dest="python_command", default=sys.executable)
    parser.add_argument(
        "--directory",
        default=str(AGENT_DIR.parent / "external" / "market-data-tools"),
    )
    parser.add_argument("--server-module", default="backend.mcp_server")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--smoke-query",
        default="",
        help="After tool discovery, execute one real unified_search query.",
    )
    return parser.parse_args()


async def _check(args: argparse.Namespace) -> tuple[list[str], int | None]:
    configured = McpAiSearchClient.from_repository(
        command=args.python_command,
        server_module=args.server_module,
        working_directory=args.directory,
        timeout_seconds=args.timeout,
    )
    if not configured.is_configured:
        raise RuntimeError(
            f"MCP 工作目录或命令不可用：{configured.command} @ {configured.working_directory}"
        )

    transport = StdioTransport(
        command=configured.command,
        args=configured.args,
        env=_build_mcp_child_env(configured.env),
        cwd=configured.working_directory,
        keep_alive=False,
    )
    async with Client(
        transport,
        name="fx-debate-mcp-preflight",
        timeout=args.timeout,
        init_timeout=max(args.timeout, 30.0),
    ) as client:
        tools = await client.list_tools()
        names = [str(tool.name) for tool in tools]
        smoke_rows: int | None = None
        if args.smoke_query:
            result = await client.call_tool(
                "unified_search",
                arguments={"query": args.smoke_query, "max_rows": 1},
                timeout=args.timeout,
                raise_on_error=False,
            )
            payload = _extract_mcp_payload(result)
            if str(payload.get("status") or "") != "success":
                error = payload.get("error")
                detail = error.get("message") if isinstance(error, dict) else error
                raise RuntimeError(
                    f"MCP smoke query returned {payload.get('status') or 'unknown'}: "
                    f"{detail or payload.get('message') or 'no detail'}"
                )
            data = payload.get("data")
            smoke_rows = len(data) if isinstance(data, list) else 0
    return names, smoke_rows


def main() -> int:
    args = _arguments()
    try:
        names, smoke_rows = asyncio.run(
            asyncio.wait_for(_check(args), timeout=args.timeout + 5.0)
        )
    except Exception as exc:  # noqa: BLE001 - CLI must return one actionable error
        print(f"MCP preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if "unified_search" not in names:
        print(
            "MCP preflight failed: unified_search tool is not registered "
            f"(available: {', '.join(names) or 'none'})",
            file=sys.stderr,
        )
        return 1
    if args.smoke_query:
        print(f"MCP preflight passed: unified_search smoke query (rows={smoke_rows or 0})")
    else:
        print("MCP preflight passed: unified_search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
