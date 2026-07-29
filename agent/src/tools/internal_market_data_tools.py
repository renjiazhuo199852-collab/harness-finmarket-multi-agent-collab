"""供 Agent 调用的四个 Phase 2 内部市场数据 Tool。

每个 Tool 只承担 Agent 输入、统一 JSON 输出和错误边界；具体 SQL 与跨表
关系集中在 :mod:`src.market_data_reader`。数据库未配置时，自动发现机制会
隐藏这些 Tool，因而普通开源安装不会看到无法使用的内部数据能力。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from src.agent.tools import BaseTool
from src.market_data_reader import MarketDataReader, MarketDataReaderError
from src.market_database import MarketDatabaseUnavailable


class _InternalMarketDataTool(BaseTool):
    """四个内部市场数据 Tool 的共享执行和序列化逻辑。"""

    def __init__(self, reader: MarketDataReader | None = None) -> None:
        # 注入点仅用于单元测试；生产环境由 Reader 继续使用只读数据库客户端。
        self._reader = reader or MarketDataReader()

    @classmethod
    def check_available(cls) -> bool:
        """仅在本机显式启用并配置市场数据库时注册 Tool。"""
        return MarketDataReader().is_configured

    def _execute_reader(
        self, operation: Callable[..., dict[str, Any]], **kwargs: Any
    ) -> str:
        """把 Reader 结果转换为稳定的 Tool JSON 成功或失败信封。"""
        try:
            payload = operation(**kwargs)
        except (MarketDataReaderError, MarketDatabaseUnavailable) as exc:
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001 - Tool 不应把未捕获异常抛给 Agent
            return _error(f"内部市场数据查询失败：{exc}")
        return json.dumps(
            {"ok": True, "data": payload}, ensure_ascii=False, default=_json_default
        )


class GetMarketBarsTool(_InternalMarketDataTool):
    """查询一个内部标准工具代码的历史 K 线。"""

    name = "get_market_bars"
    description = (
        "从内部 Phase 2 PostgreSQL 读取一个工具的历史 OHLCV K 线。"
        "symbol 使用标准代码，例如 EURUSD；可按 LSEG 等 source、daily 等 "
        "frequency 和日期范围筛选。结果通过 instrument_id 关联，供应商 RIC "
        "只用于追溯。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "标准工具代码，例如 EURUSD。"},
            "source": {"type": "string", "description": "可选供应商，例如 LSEG。"},
            "frequency": {
                "type": "string",
                "enum": sorted(
                    [
                        "realtime",
                        "tick",
                        "minute",
                        "hourly",
                        "daily",
                        "weekly",
                        "monthly",
                        "quarterly",
                        "yearly",
                    ]
                ),
                "description": "可选 K 线频率，例如 daily。",
            },
            "start_date": {
                "type": "string",
                "description": "可选起始日期，YYYY-MM-DD。",
            },
            "end_date": {"type": "string", "description": "可选结束日期，YYYY-MM-DD。"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 250,
                "description": "最多返回的最近 K 线条数。",
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        """执行历史 K 线只读查询。"""
        return self._execute_reader(
            self._reader.get_market_bars,
            symbol=kwargs.get("symbol"),
            source=kwargs.get("source"),
            frequency=kwargs.get("frequency"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            limit=kwargs.get("limit", 250),
        )


class GetLatestPricesTool(_InternalMarketDataTool):
    """查询一个内部标准工具代码的最新报价快照。"""

    name = "get_latest_prices"
    description = (
        "从内部 Phase 2 PostgreSQL 读取一个工具的当前报价快照。"
        "同一 instrument_id 与 source 仅保留一条记录；symbol 使用标准代码，"
        "例如 EURUSD。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "标准工具代码，例如 EURUSD。"},
            "source": {
                "type": "string",
                "description": "可选供应商，例如 LSEG；省略时返回该工具的所有供应商报价。",
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        """执行当前报价只读查询。"""
        return self._execute_reader(
            self._reader.get_latest_prices,
            symbol=kwargs.get("symbol"),
            source=kwargs.get("source"),
        )


class GetMacroObservationsTool(_InternalMarketDataTool):
    """查询一个工具正式关联的宏观指标发布数据。"""

    name = "get_macro_observations"
    description = (
        "从内部 Phase 2 PostgreSQL 读取与某工具正式关联的宏观指标发布记录。"
        "关联规则来自 instrument_metric_link，真实数值来自 macro_observations；"
        "symbol 使用标准代码，例如 EURUSD。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "标准工具代码，例如 EURUSD。"},
            "metric_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": '可选指标代码列表，例如 ["US_INFLATION_CPI_YOY"]。',
            },
            "source": {"type": "string", "description": "可选供应商，例如 LSEG。"},
            "start_date": {
                "type": "string",
                "description": "可选起始日期，YYYY-MM-DD。",
            },
            "end_date": {"type": "string", "description": "可选结束日期，YYYY-MM-DD。"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 100,
                "description": "最多返回的最近发布记录条数。",
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        """执行宏观指标发布记录只读查询。"""
        return self._execute_reader(
            self._reader.get_macro_observations,
            symbol=kwargs.get("symbol"),
            metric_ids=kwargs.get("metric_ids"),
            source=kwargs.get("source"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            limit=kwargs.get("limit", 100),
        )


class GetNewsTool(_InternalMarketDataTool):
    """查询一个内部标准工具代码正式关联的新闻。"""

    name = "get_news"
    description = (
        "从内部 Phase 2 PostgreSQL 读取与某工具正式关联的新闻。"
        "查询经 news_instrument_link 的内连接完成，不从 keywords JSONB 猜测关联；"
        "symbol 使用标准代码，例如 EURUSD。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "标准工具代码，例如 EURUSD。"},
            "source": {"type": "string", "description": "可选供应商，例如 LSEG。"},
            "start_date": {
                "type": "string",
                "description": "可选起始日期，YYYY-MM-DD。",
            },
            "end_date": {"type": "string", "description": "可选结束日期，YYYY-MM-DD。"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "default": 50,
                "description": "最多返回的最近新闻条数。",
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        """执行正式关联新闻的只读查询。"""
        return self._execute_reader(
            self._reader.get_news,
            symbol=kwargs.get("symbol"),
            source=kwargs.get("source"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            limit=kwargs.get("limit", 50),
        )


def _error(message: str) -> str:
    """将预期失败统一渲染为 Agent 可处理的 JSON 错误信封。"""
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _json_default(value: Any) -> Any:
    """将 PostgreSQL 驱动返回的日期和数值转换为标准 JSON 值。

    价格和宏观数值以 ``float`` 返回，便于研究型 Agent 直接比较、计算；本
    Tool 只读且不承担交易下单精度，因此不把 Decimal 作为字符串传递给调用方。
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"不能序列化 {type(value).__name__} 类型的内部市场数据字段。")
