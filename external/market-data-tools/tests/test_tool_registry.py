"""五个工具公共接口的协议测试，不访问真实数据库。"""

from __future__ import annotations

import sys
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))

from backend.ai_search import get_tool_definitions, invoke_tool  # noqa: E402
from backend.ai_search import tool_registry  # noqa: E402


def test_definitions_expose_exactly_five_tools() -> None:
    """Agent 注册表只暴露统一工具和四个专用工具。"""

    definitions = get_tool_definitions()
    names = [item["function"]["name"] for item in definitions]
    assert names == [
        "unified_search",
        "latest_prices_search",
        "market_bars_search",
        "macro_observations_search",
        "news_articles_search",
    ]


def test_unknown_tool_returns_structured_error() -> None:
    """未知工具不抛出未处理异常，始终返回统一错误协议。"""

    result = invoke_tool("unknown", {"query": "测试"})
    assert result["status"] == "error"
    assert result["data"] == []
    assert result["code"] == "TOOL_NOT_FOUND"


def test_public_wrapper_hides_internal_result(monkeypatch) -> None:
    """公共函数只返回业务数据，不把目录和阶段详情泄露给 Agent。"""

    monkeypatch.setattr(
        tool_registry,
        "run_tool_internal",
        lambda *_args, **_kwargs: {
            "status": "success",
            "adapter": "latest_prices",
            "execution": {
                "status": "resolved",
                "adapter": "latest_prices",
                "rows": [{"last": "1.15", "bid": "1.14"}],
            },
            "dataset_search": {"candidates": [{"dataset_id": "SECRET_INTERNAL"}]},
        },
    )
    result = tool_registry.latest_prices_search("EURUSD")
    assert result == {
        "status": "success",
        "data": [{"last": "1.15", "bid": "1.14"}],
    }
