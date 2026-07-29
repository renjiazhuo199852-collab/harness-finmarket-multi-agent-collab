"""手工验证四个 Phase 2 内部市场数据 Tool 是否能读取真实 PostgreSQL。

此脚本用于成员本机的集成冒烟测试：它从 ``agent/.env`` 读取已被 Git 忽略的
数据库配置，直接调用四个已经注册到 Agent 的 Tool，并输出稳定的验证摘要。
脚本不调用 LLM，因此不会消耗模型 API Key 额度；也不向 PostgreSQL 写入任何数据。

运行前必须完成：
1. 在仓库根目录安装 ``pip install -e \".[market-db,dev]\"``；
2. 建立 SSH 隧道，使本机 ``MARKET_DB_HOST:MARKET_DB_PORT`` 可访问数据库；
3. 创建 ``agent/.env`` 并填入完整的 ``MARKET_DB_*`` 配置。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# 脚本位于 ``agent/scripts``，直接以 ``python agent/scripts/...`` 运行时，
# Python 默认只会把 scripts 目录放入导入路径。显式加入 agent 源码根目录后，
# 成员即使尚未执行 editable 安装，也能使用本仓库的 ``src`` 模块完成诊断；
# psycopg 等第三方依赖仍必须按本文档安装，不能由此绕过。
_AGENT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(_AGENT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIRECTORY))


def _parse_arguments() -> argparse.Namespace:
    """解析手工验证所需的少量筛选参数。

    默认值与当前 Phase 2 样例数据保持一致。成员可以传入其他已登记的标准工具
    代码和供应商，以验证自己导入的数据；脚本不会把测试目标写死为 EURUSD。
    """
    parser = argparse.ArgumentParser(
        description="通过真实 PostgreSQL 验证四个 Phase 2 内部市场数据 Tool。"
    )
    parser.add_argument(
        "--symbol",
        default="EURUSD",
        help="内部标准工具代码，默认 EURUSD。",
    )
    parser.add_argument(
        "--source",
        default="LSEG",
        help="可选供应商筛选，默认 LSEG；传空字符串时不按供应商筛选。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="K 线、宏观和新闻查询的最多返回条数，默认 10。",
    )
    parser.add_argument(
        "--require-data",
        action="store_true",
        help="要求每个 Tool 至少返回一条记录；适合验证已导入样例或正式数据的环境。",
    )
    parser.add_argument(
        "--show-data",
        action="store_true",
        help="打印每个 Tool 的完整 JSON 结果；默认只打印安全的记录数摘要。",
    )
    return parser.parse_args()


def _load_local_environment() -> Path:
    """加载仓库内 ``agent/.env``，而不要求调用者先手工导出环境变量。

    ``.env`` 不存在时不立即抛错：成员也可以在终端中设置环境变量。后续由
    ``MarketDataReader.is_configured`` 给出统一的完整配置检查和明确提示。
    """
    env_path = _AGENT_DIRECTORY / ".env"
    if env_path.is_file():
        # override=False 保留调用者显式设置的终端环境变量，符合 dotenv 的常规优先级。
        load_dotenv(env_path, override=False)
        print(f"已加载本机配置：{env_path}")
    else:
        print(f"未找到 {env_path}；将只读取当前终端已设置的环境变量。")
    return env_path


def _run_tool(
    tool_name: str,
    tool: Any,
    arguments: dict[str, Any],
    *,
    require_data: bool,
    show_data: bool,
) -> bool:
    """执行一个 Tool，并将统一 JSON 信封转换为人类可读的验证结果。

    Tool 自身已经把预期错误包装为 ``{\"ok\": false}``。这里不捕获后再伪造
    成功，而是把解析失败、Tool 错误或缺少数据都作为明确的失败信号返回给调用者。
    """
    raw_result = tool.execute(**arguments)
    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError:
        print(f"[FAIL] {tool_name}: Tool 未返回合法 JSON。")
        print(raw_result)
        return False

    if not result.get("ok"):
        print(f"[FAIL] {tool_name}: {result.get('error', '未知 Tool 错误')}")
        return False

    data = result.get("data")
    if not isinstance(data, dict):
        print(f"[FAIL] {tool_name}: 成功信封中的 data 不是对象。")
        return False

    count = data.get("count")
    if not isinstance(count, int):
        print(f"[FAIL] {tool_name}: 返回结果没有整数 count 字段。")
        return False

    if require_data and count == 0:
        print(
            f"[FAIL] {tool_name}: 查询成功但没有匹配记录，--require-data 要求至少一条。"
        )
        return False

    print(f"[PASS] {tool_name}: 查询成功，返回 {count} 条记录。")
    if show_data:
        # 完整结果可能含新闻正文，因此默认不打印；操作者显式要求时才展示。
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return True


def main() -> int:
    """按照与真实 Agent 相同的 Tool 接口依次验证四条查询路径。"""
    arguments = _parse_arguments()
    _load_local_environment()

    # 必须在加载 .env 后导入 Reader：环境配置采用懒加载单例，先导入可以确保
    # 当前进程读取到的就是本机 .env 或调用者显式导出的环境变量。
    from src.market_data_reader import MarketDataReader
    from src.tools.internal_market_data_tools import (
        GetLatestPricesTool,
        GetMacroObservationsTool,
        GetMarketBarsTool,
        GetNewsTool,
    )

    reader = MarketDataReader()
    if not reader.is_configured:
        print(
            "[FAIL] 市场数据库未完整配置。请设置 MARKET_DB_ENABLED=1，并填写 "
            "MARKET_DB_HOST、MARKET_DB_NAME、MARKET_DB_USER、MARKET_DB_PASSWORD。"
        )
        return 2

    # 空字符串意味着不按供应商筛选，与 Agent Tool 的可选 source 参数语义一致。
    source = arguments.source or None
    shared = {"symbol": arguments.symbol, "source": source}
    checks: tuple[tuple[str, Any, dict[str, Any]], ...] = (
        (
            "get_market_bars",
            GetMarketBarsTool(reader),
            {**shared, "frequency": "daily", "limit": arguments.limit},
        ),
        ("get_latest_prices", GetLatestPricesTool(reader), shared),
        (
            "get_macro_observations",
            GetMacroObservationsTool(reader),
            {**shared, "limit": arguments.limit},
        ),
        ("get_news", GetNewsTool(reader), {**shared, "limit": arguments.limit}),
    )

    failures = 0
    for tool_name, tool, tool_arguments in checks:
        passed = _run_tool(
            tool_name,
            tool,
            tool_arguments,
            require_data=arguments.require_data,
            show_data=arguments.show_data,
        )
        failures += int(not passed)

    if failures:
        print(f"验证失败：{failures} 个 Tool 未通过。")
        return 1

    print("验证成功：四个 Phase 2 内部市场数据 Tool 均可读取真实 PostgreSQL。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
