"""Top-level natural-language FX data query Tool.

This Tool is a client of the independent AI Search service. It deliberately
does not expose table names, fields, SQL, or the provider's internal routes.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

from src.agent.tools import BaseTool
from src.config.accessor import get_env_config
from src.fx_debate.data_query_agent import (
    AiSearchClient,
    FxDataQueryAgent,
    FxDataServiceError,
)


class QueryFxDataTool(BaseTool):
    """Answer direct, natural-language FX data questions."""

    name = "query_fx_data"
    description = (
        "通过独立智能数据检索服务查询外汇行情、历史K线、宏观数据或新闻。"
        "输入自然语言问题，服务端负责模糊匹配货币对、数据集和供应商；"
        "不要传 SQL、表名或数据库字段。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "自然语言数据问题，例如：查询 EURUSD 最近一个月的日线行情。",
            },
            "domain": {
                "type": "string",
                "enum": ["unified", "prices", "bars", "macro", "news"],
                "default": "unified",
                "description": "可选数据领域；不确定时使用 unified。",
            },
            "provider": {"type": "string", "description": "可选数据供应商。"},
            "start_date": {"type": "string", "description": "可选 YYYY-MM-DD 起始日期。"},
            "end_date": {"type": "string", "description": "可选 YYYY-MM-DD 结束日期。"},
            "max_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 250,
            },
        },
        "required": ["query"],
    }
    is_readonly = True
    repeatable = True

    def __init__(self, *, event_callback: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        config = get_env_config().fx_debate
        trace_callback = None
        if event_callback is not None:
            def trace_callback(event: dict[str, Any]) -> None:
                event_callback(str(event.get("type") or "data_service.event"), event)

        self._agent = FxDataQueryAgent(
            AiSearchClient(
                config.data_service_url,
                timeout_seconds=config.data_service_timeout_seconds,
                max_rows=config.data_service_max_rows,
                trace_callback=trace_callback,
            )
        )

    @classmethod
    def check_available(cls) -> bool:
        config = get_env_config().fx_debate
        return bool(
            (config.data_service_enabled or config.data_source == "ai_search")
            and config.data_service_url.startswith(("http://", "https://"))
        )

    def execute(self, **kwargs: Any) -> str:
        try:
            result = self._agent.query(
                str(kwargs.get("query") or ""),
                domain=str(kwargs.get("domain") or "unified"),
                provider=kwargs.get("provider"),
                start_date=_optional_date(kwargs.get("start_date")),
                end_date=_optional_date(kwargs.get("end_date")),
                max_rows=kwargs.get("max_rows"),
            )
            return json.dumps(
                {
                    "status": "success",
                    "tool": self.name,
                    "data": result.get("data", []),
                    "meta": {
                        "domain": kwargs.get("domain") or "unified",
                        "schema_version": result.get("schema_version"),
                        "provider_meta": result.get("meta", {}),
                    },
                },
                ensure_ascii=False,
            )
        except FxDataServiceError as exc:
            return json.dumps(
                {
                    "status": "error",
                    "tool": self.name,
                    "code": exc.code,
                    "message": str(exc),
                    "data": [],
                },
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            return json.dumps(
                {
                    "status": "error",
                    "tool": self.name,
                    "code": "INVALID_DATA_QUERY",
                    "message": str(exc),
                    "data": [],
                },
                ensure_ascii=False,
            )


def _optional_date(value: Any) -> date | str | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return str(value)
