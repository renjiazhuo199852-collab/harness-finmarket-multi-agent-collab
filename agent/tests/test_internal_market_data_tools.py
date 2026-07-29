"""四个内部市场数据 Agent Tool 的输入输出与注册测试。"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from src.market_data_reader import MarketDataReaderError
from src import tools as tools_package
from src.tools import internal_market_data_tools
from src.tools.internal_market_data_tools import (
    GetLatestPricesTool,
    GetMacroObservationsTool,
    GetMarketBarsTool,
    GetNewsTool,
)


class _FakeReader:
    """为 Tool 提供包含 PostgreSQL 常见日期与 Decimal 类型的测试结果。"""

    is_configured = True

    def get_market_bars(self, **kwargs: Any) -> dict[str, Any]:
        """返回一条 K 线和回显参数，便于验证 Tool 转发逻辑。"""
        return {
            "bars": [
                {
                    "bar_date": date(2024, 1, 2),
                    "bar_time": datetime(2024, 1, 2, tzinfo=timezone.utc),
                    "close": Decimal("1.10300000"),
                }
            ],
            "received": kwargs,
        }

    def get_latest_prices(self, **kwargs: Any) -> dict[str, Any]:
        """返回当前报价的最小样例。"""
        return {"prices": [{"bid": Decimal("1.10320000")}], "received": kwargs}

    def get_macro_observations(self, **kwargs: Any) -> dict[str, Any]:
        """返回宏观发布记录的最小样例。"""
        return {"observations": [{"value": Decimal("3.2")}], "received": kwargs}

    def get_news(self, **kwargs: Any) -> dict[str, Any]:
        """返回新闻记录的最小样例。"""
        return {"articles": [{"title": "ECB update"}], "received": kwargs}


def test_market_bars_tool_serializes_postgresql_values_and_forwards_inputs() -> None:
    """日期和 Decimal 必须转换为标准 JSON，且参数不应丢失。"""
    tool = GetMarketBarsTool(reader=_FakeReader())

    output = json.loads(
        tool.execute(
            symbol="EURUSD",
            source="LSEG",
            frequency="daily",
            start_date="2024-01-01",
            end_date="2024-01-02",
            limit=2,
        )
    )

    assert output["ok"] is True
    row = output["data"]["bars"][0]
    assert row["bar_date"] == "2024-01-02"
    assert row["bar_time"] == "2024-01-02T00:00:00+00:00"
    assert row["close"] == 1.103
    assert output["data"]["received"]["source"] == "LSEG"


def test_each_tool_uses_its_own_reader_operation() -> None:
    """四个公开 Tool 名称应分别映射到四张业务表对应的 Reader 方法。"""
    reader = _FakeReader()

    latest = json.loads(GetLatestPricesTool(reader=reader).execute(symbol="EURUSD"))
    macro = json.loads(
        GetMacroObservationsTool(reader=reader).execute(
            symbol="EURUSD", metric_ids=["US_INFLATION_CPI_YOY"]
        )
    )
    news = json.loads(GetNewsTool(reader=reader).execute(symbol="EURUSD", limit=3))

    assert latest["ok"] is True
    assert latest["data"]["prices"][0]["bid"] == 1.1032
    assert macro["ok"] is True
    assert macro["data"]["received"]["metric_ids"] == ["US_INFLATION_CPI_YOY"]
    assert news["ok"] is True
    assert news["data"]["received"]["limit"] == 3


def test_tool_returns_clean_error_envelope_for_expected_reader_error() -> None:
    """用户输入或主数据错误不能以异常形式泄漏出 Tool 边界。"""

    class _FailingReader(_FakeReader):
        def get_news(self, **kwargs: Any) -> dict[str, Any]:
            raise MarketDataReaderError("未找到标准工具代码 'UNKNOWN'。")

    output = json.loads(GetNewsTool(reader=_FailingReader()).execute(symbol="UNKNOWN"))

    assert output == {"ok": False, "error": "未找到标准工具代码 'UNKNOWN'。"}


def test_tools_are_hidden_until_market_database_is_fully_configured(
    monkeypatch,
) -> None:
    """未配置数据库的普通安装不应向 Agent 注册无法工作的内部 Tool。"""
    for key in (
        "MARKET_DB_ENABLED",
        "MARKET_DB_HOST",
        "MARKET_DB_NAME",
        "MARKET_DB_USER",
        "MARKET_DB_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)

    assert GetMarketBarsTool.check_available() is False
    assert GetLatestPricesTool.check_available() is False
    assert GetMacroObservationsTool.check_available() is False
    assert GetNewsTool.check_available() is False


def test_tools_are_available_when_market_database_is_configured(monkeypatch) -> None:
    """完整本机配置只决定 Tool 是否注册，不会在检查时连接数据库。"""
    monkeypatch.setenv("MARKET_DB_ENABLED", "true")
    monkeypatch.setenv("MARKET_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("MARKET_DB_PORT", "15433")
    monkeypatch.setenv("MARKET_DB_NAME", "icbc_shared")
    monkeypatch.setenv("MARKET_DB_USER", "icbc_collab")
    monkeypatch.setenv("MARKET_DB_PASSWORD", "test-password")

    assert GetMarketBarsTool.check_available() is True
    assert GetLatestPricesTool.check_available() is True
    assert GetMacroObservationsTool.check_available() is True
    assert GetNewsTool.check_available() is True


def test_configured_tools_are_auto_discovered_by_the_local_registry(
    monkeypatch,
) -> None:
    """完整配置后，Agent 本地注册表应真正暴露四个 Tool，而不只是在类上可用。"""
    monkeypatch.setenv("MARKET_DB_ENABLED", "true")
    monkeypatch.setenv("MARKET_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("MARKET_DB_PORT", "15433")
    monkeypatch.setenv("MARKET_DB_NAME", "icbc_shared")
    monkeypatch.setenv("MARKET_DB_USER", "icbc_collab")
    monkeypatch.setenv("MARKET_DB_PASSWORD", "test-password")

    # 自动发现结果在进程中缓存。测试临时清空缓存，验证新的 Tool 文件确实
    # 会被导入并注册；结束后恢复原缓存，避免影响其他测试模块的执行顺序。
    previous_cache = tools_package._SUBCLASSES_CACHE
    try:
        tools_package._SUBCLASSES_CACHE = None
        registry = tools_package.build_registry()
    finally:
        tools_package._SUBCLASSES_CACHE = previous_cache

    assert {
        "get_market_bars",
        "get_latest_prices",
        "get_macro_observations",
        "get_news",
    }.issubset(registry.tool_names)


def test_tool_metadata_matches_public_contract() -> None:
    """Tool 名称和只读属性是 Agent 发现与使用的稳定契约。"""
    tools = [
        GetMarketBarsTool(),
        GetLatestPricesTool(),
        GetMacroObservationsTool(),
        GetNewsTool(),
    ]

    assert [tool.name for tool in tools] == [
        "get_market_bars",
        "get_latest_prices",
        "get_macro_observations",
        "get_news",
    ]
    assert all(tool.is_readonly for tool in tools)
    assert internal_market_data_tools._json_default(Decimal("1.25")) == 1.25
