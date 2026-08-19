"""Agent 可调用的 AI Search 工具包。

这里集中导出五个稳定的 Python 工具函数。查询细节仍由内部编排器、目录和
业务适配器完成，Agent 不需要知道物理表名、字段名或 SQL。
"""

from .tool_registry import (
    get_tool_definitions,
    invoke_tool,
    latest_prices_search,
    macro_observations_search,
    market_bars_search,
    news_articles_search,
    unified_search,
)

__all__ = [
    "get_tool_definitions",
    "invoke_tool",
    "latest_prices_search",
    "market_bars_search",
    "macro_observations_search",
    "news_articles_search",
    "unified_search",
]
