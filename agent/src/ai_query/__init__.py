"""AI 驱动的目录检索与安全查询核心。

这个包故意与现有四个 Phase 2 市场数据 Tool 分开：旧 Tool 继续使用
``MARKET_DB_*`` 连接远端 Phase 2 数据库，本包使用 ``AI_QUERY_*`` 连接本机
``icbc_finmarket_ai``，先从目录和检索文档中找到候选，再执行受控查询计划。
"""

from src.ai_query.catalog_search import AICatalogSearch, EmbeddingUnavailable
from src.ai_query.query_executor import AIQueryPlanError, AIQueryExecutor

__all__ = [
    "AICatalogSearch",
    "AIQueryExecutor",
    "AIQueryPlanError",
    "EmbeddingUnavailable",
]
