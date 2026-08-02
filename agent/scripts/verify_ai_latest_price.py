"""验证 AI 目录检索和 EUR/USD 最新报价查询链路。

这个脚本不启动 LLM，也不让模型生成 SQL；它用于先验证数据库基础设施和
结构化查询执行器。检索阶段会真实执行精确/关键词/Embedding 候选查询，
执行阶段使用一份由测试脚本明确构造的结构化计划，方便定位问题属于“找表”
还是“执行查询”。
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from src.ai_query import AICatalogSearch, AIQueryExecutor
from src.config.accessor import get_env_config


def main() -> int:
    """打印检索候选和 EUR/USD 最新报价结果。"""
    repository_root = Path(__file__).resolve().parents[2]
    # 项目开发约定把本机凭据放在 agent/.env；同时保留根目录 .env 作为兼容
    # 入口。两个文件都只加载到当前进程，不会把密码写回仓库或输出到终端。
    # 先加载根目录，再加载 agent/.env，并允许后者覆盖前者，方便成员按
    # agent/.env.example 配置本项目专用的 AI 查询数据库。
    load_dotenv(repository_root / ".env")
    load_dotenv(repository_root / "agent" / ".env", override=True)
    config = get_env_config().ai_query
    if not config.is_configured():
        raise SystemExit(
            "AI 查询数据库未配置，请设置 AI_QUERY_ENABLED、AI_QUERY_DB_* 和密码。"
        )

    question = "查询 EUR/USD 最新价格"
    search_result = AICatalogSearch(config=config).search(question, limit=10)
    print("目录检索结果：")
    print(json.dumps(search_result, ensure_ascii=False, indent=2))

    # 计划字段全部使用 dataset_field_catalog 中的业务字段名；执行器会先
    # 读取 ai.field_mapping，再把它们映射成 source.latest_prices 的实际列。
    plan = {
        "dataset_id": "LSEG_SPOT_PRICE",
        "entity": {"type": "instrument", "value": "EURUSD"},
        "select": ["PRICE_TIME", "LAST", "BID", "ASK", "MID"],
        "filters": [
            {"field": "SOURCE", "operator": "eq", "value": "LSEG"},
        ],
        "order_by": [{"field": "PRICE_TIME", "direction": "desc"}],
        "limit": 1,
    }
    result = AIQueryExecutor(config=config).execute(plan)
    print("报价查询结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
