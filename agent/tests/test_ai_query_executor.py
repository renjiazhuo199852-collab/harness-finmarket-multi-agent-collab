"""结构化查询计划校验和 EUR/USD 报价执行测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.ai_query.query_executor import AIQueryExecutor, AIQueryPlanError
from src.config.env_schema import AIQueryConfig


def _config() -> AIQueryConfig:
    """返回只用于单元测试的配置，并把时效阈值放宽。"""
    return AIQueryConfig(
        enabled=True,
        host="127.0.0.1",
        port=5432,
        database="icbc_finmarket_ai",
        user="postgres",
        password="test-password",
        stale_after_seconds=604800,
    )


class _QueryClient:
    """按查询目标返回目录、工具主数据和报价假数据。"""

    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def fetch_all(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        if "FROM ai.dataset_policy" in query:
            return [
                {
                    "dataset_id": "LSEG_SPOT_PRICE",
                    "storage_table_name": "latest_prices",
                    "is_queryable": True,
                    "source_version": "db_export_0802",
                }
            ]
        if "FROM ai.field_mapping" in query:
            return [
                {
                    "source_field_name": name,
                    "storage_schema": "source",
                    "storage_table": "latest_prices",
                    "storage_column": column,
                    "is_filterable": True,
                    "is_selectable": True,
                }
                for name, column in (
                    ("PRICE_TIME", "price_time"),
                    ("LAST", "last"),
                    ("BID", "bid"),
                    ("ASK", "ask"),
                    ("MID", "mid"),
                    ("SOURCE", "source"),
                )
            ]
        if "source.instrument_master" in query:
            return [
                {
                    "instrument_id": "FX_EURUSD",
                    "canonical_symbol": "EUR/USD",
                    "name": "EUR/USD Spot",
                    "description": "EUR/USD spot exchange rate",
                    "provider": "LSEG",
                    "identifier_type": "RIC",
                    "identifier": "EUR=",
                }
            ]
        if 'FROM "source"."latest_prices"' in query:
            return [
                {
                    "price_time": datetime.now(timezone.utc),
                    "last": Decimal("1.1528"),
                    "bid": Decimal("1.1527"),
                    "ask": Decimal("1.1529"),
                    "mid": Decimal("1.1528"),
                }
            ]
        raise AssertionError(f"unexpected query: {query}")


def _plan(symbol: str = "EURUSD") -> dict[str, Any]:
    """返回 leader 要求的结构化报价计划。"""
    return {
        "dataset_id": "LSEG_SPOT_PRICE",
        "entity": {"type": "instrument", "value": symbol},
        "select": ["PRICE_TIME", "LAST", "BID", "ASK", "MID"],
        "filters": [{"field": "SOURCE", "operator": "eq", "value": "LSEG"}],
        "order_by": [{"field": "PRICE_TIME", "direction": "desc"}],
        "limit": 1,
    }


def test_executor_runs_metadata_driven_eurusd_query() -> None:
    """执行器通过目录和关系生成报价查询，不把 EURUSD 拼进 SQL。"""
    client = _QueryClient()
    result = AIQueryExecutor(client, config=_config()).execute(_plan())

    assert result["ok"] is True
    assert result["dataset_id"] == "LSEG_SPOT_PRICE"
    assert result["storage_table_name"] == "latest_prices"
    assert result["source_version"] == "db_export_0802"
    assert result["instrument"]["instrument_id"] == "FX_EURUSD"
    assert result["data"][0]["last"] == 1.1528
    query, params = client.calls[-1]
    assert 'JOIN "source"."instrument_identifier"' in query
    assert "EURUSD" not in query
    assert "FX_EURUSD" in params
    assert "LSEG" in params
    assert params[-1] == 1


def test_executor_accepts_eurusd_with_slash() -> None:
    """EUR/USD 和 EURUSD 必须解析到同一内部工具。"""
    result = AIQueryExecutor(_QueryClient(), config=_config()).execute(_plan("EUR/USD"))
    assert result["instrument"]["instrument_id"] == "FX_EURUSD"


def test_executor_maps_range_operators_to_sql_symbols() -> None:
    """结构化计划的 gte/lte 等运算符必须映射为合法 SQL 符号。"""
    client = _QueryClient()
    plan = _plan()
    plan["filters"] = [
        {"field": "PRICE_TIME", "operator": "gte", "value": "2026-01-01"}
    ]

    AIQueryExecutor(client, config=_config()).execute(plan)

    query, params = client.calls[-1]
    assert '"price_time" >= %s' in query
    assert " GTE " not in query
    assert "2026-01-01" in params


@pytest.mark.parametrize(
    "plan",
    [
        {"sql": "DROP TABLE source.latest_prices"},
        {**_plan(), "select": ["UNKNOWN_FIELD"]},
        {**_plan(), "dataset_id": "UNREGISTERED"},
        {**_plan(), "limit": 101},
    ],
)
def test_executor_rejects_unsafe_or_invalid_plans(plan: dict[str, Any]) -> None:
    """原始 SQL、未知字段、未登记数据集和超限返回必须在查询前拒绝。"""
    with pytest.raises(AIQueryPlanError):
        AIQueryExecutor(_QueryClient(), config=_config()).execute(plan)
