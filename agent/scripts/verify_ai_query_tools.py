"""验证 AI 查询 Tool 的注册和真实本机数据库调用。

这个脚本不调用 LLM，因此适合先排查“Tool 是否注册”和“数据库查询是否
成功”。真实 AgentLoop 的 Function Calling 验证仍需要在完整项目环境中运行。
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from src.config.accessor import get_env_config
from src.tools import build_registry


def main() -> int:
    """打印两个 AI Tool 的注册状态、目录候选和 EUR/USD 报价。"""
    repository_root = Path(__file__).resolve().parents[2]
    # 开发约定优先使用 agent/.env；根目录 .env 只作为兼容配置来源。
    # 凭据只进入当前 Python 进程，不写文件、不打印环境变量值。
    load_dotenv(repository_root / ".env")
    load_dotenv(repository_root / "agent" / ".env", override=True)

    if not get_env_config().ai_query.is_configured():
        raise SystemExit(
            "AI 查询 Tool 未配置，请设置 AI_QUERY_ENABLED、AI_QUERY_DB_* 和密码。"
        )

    registry = build_registry()
    required_tools = ("search_data_catalog", "execute_query_plan")
    missing = [name for name in required_tools if name not in registry]
    if missing:
        raise SystemExit(f"AI 查询 Tool 未注册：{', '.join(missing)}")

    print("已注册 Tool：")
    print(json.dumps(required_tools, ensure_ascii=False, indent=2))
    print("Tool Schema：")
    print(
        json.dumps(
            [
                definition
                for definition in registry.get_definitions()
                if definition["function"]["name"] in required_tools
            ],
            ensure_ascii=False,
            indent=2,
        )
    )

    search_result = registry.execute(
        "search_data_catalog",
        {"question": "查询 EUR/USD 最新价格", "limit": 10},
    )
    print("目录检索结果：")
    print(search_result)

    plan = {
        "dataset_id": "LSEG_SPOT_PRICE",
        "entity": {"type": "instrument", "value": "EURUSD"},
        "select": ["PRICE_TIME", "LAST", "BID", "ASK", "MID"],
        "filters": [{"field": "SOURCE", "operator": "eq", "value": "LSEG"}],
        "order_by": [{"field": "PRICE_TIME", "direction": "desc"}],
        "limit": 1,
    }
    execute_result = registry.execute("execute_query_plan", plan)
    print("报价查询结果：")
    print(execute_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
