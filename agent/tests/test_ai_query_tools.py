"""AI 查询 Agent Tool 的注册、Schema、输入转发和错误边界测试。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.ai_query.catalog_search import CatalogSearchError
from src.config.accessor import reset_env_config
from src.tools import build_registry
from src.tools.ai_query_tools import ExecuteQueryPlanTool, SearchDataCatalogTool


@pytest.fixture(autouse=True)
def _reset_config() -> Any:
    """避免配置单例把某个测试的 AI_QUERY_ENABLED 状态带到下一个测试。"""
    reset_env_config()
    yield
    reset_env_config()


class _FakeSearcher:
    """记录目录检索参数并返回一个最小结构化结果。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search(self, question: str, *, limit: int) -> dict[str, Any]:
        self.calls.append((question, limit))
        return {"ok": True, "question": question, "count": 1, "candidates": []}


class _FailingSearcher:
    """用于验证预期目录错误会被转换成 JSON。"""

    def search(self, _question: str, *, limit: int) -> dict[str, Any]:
        raise CatalogSearchError(f"invalid limit: {limit}")


class _FakeExecutor:
    """记录结构化计划并返回一个最小报价结果。"""

    def __init__(self) -> None:
        self.plans: list[dict[str, Any]] = []

    def execute(self, plan: dict[str, Any]) -> dict[str, Any]:
        self.plans.append(plan)
        return {"ok": True, "dataset_id": plan["dataset_id"], "data": []}


def _set_enabled_ai_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置完整但不含真实密码的 AI 查询配置。"""
    monkeypatch.setenv("AI_QUERY_ENABLED", "1")
    monkeypatch.setenv("AI_QUERY_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("AI_QUERY_DB_PORT", "5432")
    monkeypatch.setenv("AI_QUERY_DB_NAME", "icbc_finmarket_ai")
    monkeypatch.setenv("AI_QUERY_DB_USER", "test-user")
    monkeypatch.setenv("AI_QUERY_DB_PASSWORD", "test-password")


def test_search_tool_forwards_inputs_and_returns_core_json() -> None:
    """目录 Tool 只负责契约，问题和 limit 必须原样传给核心检索器。"""
    searcher = _FakeSearcher()
    output = json.loads(
        SearchDataCatalogTool(searcher=searcher).execute(
            question="查询 EUR/USD 最新价格",
            limit=10,
        )
    )

    assert output == {
        "ok": True,
        "question": "查询 EUR/USD 最新价格",
        "count": 1,
        "candidates": [],
    }
    assert searcher.calls == [("查询 EUR/USD 最新价格", 10)]


def test_search_tool_converts_core_errors_to_json() -> None:
    """无效输入不能把 Python 异常直接抛到 AgentLoop。"""
    output = json.loads(
        SearchDataCatalogTool(searcher=_FailingSearcher()).execute(
            question="EURUSD",
            limit=0,
        )
    )

    assert output == {"ok": False, "error": "invalid limit: 0"}


def test_execute_tool_adds_optional_defaults_and_forwards_plan() -> None:
    """执行 Tool 为省略的可选数组补默认值，再交给计划执行器校验。"""
    executor = _FakeExecutor()
    output = json.loads(
        ExecuteQueryPlanTool(executor=executor).execute(
            dataset_id="LSEG_SPOT_PRICE",
            entity={"type": "instrument", "value": "EUR/USD"},
            select=["PRICE_TIME", "LAST"],
            run_dir="C:\\temporary\\agent-run",
        )
    )

    assert output == {"ok": True, "dataset_id": "LSEG_SPOT_PRICE", "data": []}
    assert executor.plans == [
        {
            "dataset_id": "LSEG_SPOT_PRICE",
            "entity": {"type": "instrument", "value": "EUR/USD"},
            "select": ["PRICE_TIME", "LAST"],
            "filters": [],
            "order_by": [],
            "limit": 1,
        }
    ]


def test_ai_query_tool_schemas_expose_only_structured_inputs() -> None:
    """两个 OpenAI Schema 都不应暴露 raw SQL、任意表名或任意 JOIN 参数。"""
    search_schema = SearchDataCatalogTool().to_openai_schema()["function"]
    execute_schema = ExecuteQueryPlanTool().to_openai_schema()["function"]

    assert search_schema["name"] == "search_data_catalog"
    assert search_schema["parameters"]["required"] == ["question"]
    assert execute_schema["name"] == "execute_query_plan"
    assert execute_schema["parameters"]["required"] == [
        "dataset_id",
        "entity",
        "select",
    ]
    assert "sql" not in execute_schema["parameters"]["properties"]
    assert "table" not in execute_schema["parameters"]["properties"]
    assert "join" not in execute_schema["parameters"]["properties"]


def test_ai_query_tools_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有显式配置时，旧项目启动不应看到两个新 Tool。"""
    for name in (
        "AI_QUERY_ENABLED",
        "AI_QUERY_DB_USER",
        "AI_QUERY_DB_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    assert SearchDataCatalogTool.check_available() is False
    assert ExecuteQueryPlanTool.check_available() is False
    registry = build_registry()
    assert "search_data_catalog" not in registry
    assert "execute_query_plan" not in registry


def test_ai_query_tools_register_when_database_is_explicitly_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整配置打开后，自动发现机制应注册两个 Tool。"""
    _set_enabled_ai_query(monkeypatch)

    assert SearchDataCatalogTool.check_available() is True
    assert ExecuteQueryPlanTool.check_available() is True
    registry = build_registry()

    assert "search_data_catalog" in registry
    assert "execute_query_plan" in registry
