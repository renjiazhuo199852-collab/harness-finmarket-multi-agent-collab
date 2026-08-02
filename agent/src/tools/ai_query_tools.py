"""AI 数据目录检索和结构化查询 Agent Tool。

这两个 Tool 只在 ``AI_QUERY_ENABLED=1`` 且本机 AI 查询数据库凭据完整时
自动注册。检索 Tool 不返回行情业务行，执行 Tool 也不接受原始 SQL；真正
的目录检索、查询计划校验和参数化 SQL 都集中在 ``src.ai_query`` 包中。
"""

from __future__ import annotations

import json
from typing import Any

from src.agent.tools import BaseTool
from src.ai_query import AICatalogSearch, AIQueryExecutor
from src.ai_query.catalog_search import CatalogSearchError
from src.ai_query.query_executor import AIQueryPlanError
from src.config.accessor import get_env_config
from src.market_database import MarketDatabaseUnavailable


class _AIQueryTool(BaseTool):
    """两个 AI 查询 Tool 共用的可用性判断和 JSON 错误边界。"""

    @classmethod
    def check_available(cls) -> bool:
        """只有配置完整时才向 Agent 暴露新 Tool。

        这里只读取配置，不建立数据库连接；实际连接会在 Agent 调用 Tool
        时由 ``MarketDatabaseClient`` 懒加载。这样默认配置不会增加启动时
        的网络依赖，也不会因为数据库暂时不可用阻塞 Agent 注册。
        """
        return get_env_config().ai_query.is_configured()

    @staticmethod
    def _success(payload: dict[str, Any]) -> str:
        """把核心模块的结构化结果序列化为 Agent 可消费的 JSON。"""
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _failure(error: Exception) -> str:
        """把预期查询错误转换成稳定错误信封，不返回原始 traceback。"""
        return json.dumps(
            {"ok": False, "error": str(error)},
            ensure_ascii=False,
        )

    @classmethod
    def _run(cls, operation: Any) -> str:
        """执行一个核心查询操作并隔离 Tool 边界异常。"""
        try:
            return cls._success(operation())
        except (CatalogSearchError, AIQueryPlanError, MarketDatabaseUnavailable) as exc:
            return cls._failure(exc)
        except Exception as exc:  # noqa: BLE001 - Tool 必须向 Agent 返回 JSON
            return cls._failure(RuntimeError(f"AI 查询工具执行失败：{exc}"))


class SearchDataCatalogTool(_AIQueryTool):
    """根据自然语言问题检索可用的数据集、字段、工具和关系目录。"""

    name = "search_data_catalog"
    description = (
        "Search the controlled AI data catalog for relevant instruments, datasets, "
        "fields, and semantic relationships. Use this before execute_query_plan. "
        "This tool returns metadata candidates only; it does not return business "
        "rows and it never accepts raw SQL."
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
                "description": "The user's natural-language data question.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
                "description": "Maximum number of catalog candidates to return.",
            },
        },
        "required": ["question"],
    }
    repeatable = True

    def __init__(self, searcher: AICatalogSearch | None = None) -> None:
        """允许测试注入检索器，生产环境使用当前本机配置构造检索器。"""
        self._searcher = searcher or AICatalogSearch()

    def execute(self, **kwargs: Any) -> str:
        """执行目录检索，不触碰 source 业务数据表。"""
        return self._run(
            lambda: self._searcher.search(
                kwargs.get("question"),
                limit=kwargs.get("limit", 10),
            )
        )


class ExecuteQueryPlanTool(_AIQueryTool):
    """执行经过白名单校验的结构化只读查询计划。"""

    name = "execute_query_plan"
    description = (
        "Execute a structured, metadata-validated read-only query plan returned "
        "by the controlled catalog workflow. Do not provide SQL, arbitrary table "
        "names, columns, or JOIN conditions. Start with search_data_catalog when "
        "the dataset or field mapping is not known."
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dataset_id": {
                "type": "string",
                "description": "Catalog dataset identifier, for example LSEG_SPOT_PRICE.",
            },
            "entity": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string", "enum": ["instrument"]},
                    "value": {
                        "type": "string",
                        "description": "Instrument code such as EURUSD or EUR/USD.",
                    },
                },
                "required": ["type", "value"],
            },
            "select": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
                "description": "Business field names returned by the catalog.",
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": ["eq", "in", "gt", "gte", "lt", "lte"],
                        },
                        "value": {},
                    },
                    "required": ["field", "operator", "value"],
                },
                "default": [],
            },
            "order_by": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field": {"type": "string"},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": ["field", "direction"],
                },
                "default": [],
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 1,
            },
        },
        "required": ["dataset_id", "entity", "select"],
    }
    repeatable = True

    def __init__(self, executor: AIQueryExecutor | None = None) -> None:
        """允许测试注入执行器，生产环境使用当前本机配置构造执行器。"""
        self._executor = executor or AIQueryExecutor()

    def execute(self, **kwargs: Any) -> str:
        """执行结构化计划；缺省筛选和排序数组由 Tool 层补成空数组。"""
        # AgentLoop 会给所有 Tool 参数注入内部 run_dir，用于普通工具保存
        # 产物；它不是查询计划的一部分，不能进入白名单校验或 SQL 生成。
        plan = {key: value for key, value in kwargs.items() if key != "run_dir"}
        plan.setdefault("filters", [])
        plan.setdefault("order_by", [])
        plan.setdefault("limit", 1)
        return self._run(lambda: self._executor.execute(plan))


__all__ = ["ExecuteQueryPlanTool", "SearchDataCatalogTool"]
