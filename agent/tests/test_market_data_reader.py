"""Phase 2 市场数据共享 Reader 的单元测试。

测试使用一个记录 SQL 与绑定参数的假的数据库客户端，确保四条查询路径
不需要真实 PostgreSQL 或 SSH 隧道，也能验证它们依赖的是正确的关联表。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.market_data_reader import MarketDataReader, MarketDataReaderError

_EURUSD = {
    "instrument_id": "FX000001",
    "canonical_symbol": "EURUSD",
    "name": "EUR/USD Spot",
    "instrument_type": "FX",
    "country": "EU",
    "region": "Europe",
    "currency": "USD",
    "status": "active",
}


class _FakeMarketDatabaseClient:
    """按查询目标返回固定数据，并保存每一次参数化 SQL 调用。"""

    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.instrument_rows: list[dict[str, Any]] = [_EURUSD]
        self.bars_rows: list[dict[str, Any]] = [{"close": 1.103, "source": "LSEG"}]
        self.price_rows: list[dict[str, Any]] = [{"bid": 1.1032, "ask": 1.1036}]
        self.macro_rows: list[dict[str, Any]] = [
            {"metric_id": "US_INFLATION_CPI_YOY", "value": 3.2}
        ]
        self.news_rows: list[dict[str, Any]] = [{"article_id": "story-001"}]

    def fetch_all(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        """模仿数据库客户端，并按固定 SQL 所涉及的表返回对应样例行。"""
        self.calls.append((query, params))
        if "FROM public.instrument_master" in query:
            return self.instrument_rows
        if "FROM public.market_bars" in query:
            return self.bars_rows
        if "FROM public.latest_prices" in query:
            return self.price_rows
        if "FROM public.instrument_metric_link AS im" in query:
            return self.macro_rows
        if "FROM public.news_articles AS n" in query:
            return self.news_rows
        raise AssertionError(f"测试未识别的 SQL：{query}")


def _reader() -> tuple[MarketDataReader, _FakeMarketDatabaseClient]:
    """创建带假数据库客户端的 Reader，避免任何网络访问。"""
    client = _FakeMarketDatabaseClient()
    return MarketDataReader(client), client


def test_market_bars_resolves_symbol_then_binds_instrument_id() -> None:
    """K 线查询应通过内部 instrument_id，而非供应商 RIC 做主要关联。"""
    reader, client = _reader()

    payload = reader.get_market_bars(
        " eurusd ",
        source="lseg",
        frequency="daily",
        start_date="2024-01-01",
        end_date="2024-01-31",
        limit=10,
    )

    assert payload["instrument"]["instrument_id"] == "FX000001"
    assert payload["bars"] == client.bars_rows
    assert payload["count"] == 1
    query, params = client.calls[-1]
    assert "FROM public.market_bars" in query
    assert "source_identifier =" not in query
    assert params == (
        "FX000001",
        "LSEG",
        "LSEG",
        "daily",
        "daily",
        "2024-01-01",
        "2024-01-01",
        "2024-01-31",
        "2024-01-31",
        10,
    )


def test_latest_prices_returns_all_sources_when_source_is_omitted() -> None:
    """报价快照可省略 source，此时查询仍从 instrument_id 开始。"""
    reader, client = _reader()

    payload = reader.get_latest_prices("EURUSD")

    assert payload["prices"] == client.price_rows
    query, params = client.calls[-1]
    assert "FROM public.latest_prices" in query
    assert params == ("FX000001", None, None)


def test_macro_observations_uses_explicit_instrument_metric_link() -> None:
    """宏观查询必须经过 Phase 2 的工具-指标关系表，而不是猜测指标。"""
    reader, client = _reader()

    payload = reader.get_macro_observations(
        "EURUSD",
        metric_ids=["us_inflation_cpi_yoy", "US_INFLATION_CPI_YOY"],
        source="LSEG",
        start_date="2024-01-01",
        end_date="2024-01-31",
        limit=20,
    )

    assert payload["observations"] == client.macro_rows
    query, params = client.calls[-1]
    assert "FROM public.instrument_metric_link AS im" in query
    assert "JOIN public.macro_observations AS mo" in query
    assert params == (
        "FX000001",
        ["US_INFLATION_CPI_YOY"],
        ["US_INFLATION_CPI_YOY"],
        "LSEG",
        "LSEG",
        "2024-01-01",
        "2024-01-01",
        "2024-01-31",
        "2024-01-31",
        20,
    )


def test_news_uses_inner_join_with_news_instrument_link() -> None:
    """新闻查询只返回正式关联 EURUSD 的文章，而不是用关键词猜测。"""
    reader, client = _reader()

    payload = reader.get_news("EURUSD", source="LSEG", limit=5)

    assert payload["articles"] == client.news_rows
    query, params = client.calls[-1]
    assert "JOIN public.news_instrument_link AS link" in query
    assert "ON link.news_id = n.id" in query
    assert params == ("FX000001", "LSEG", "LSEG", None, None, None, None, 5)


@pytest.mark.parametrize(
    ("method", "kwargs", "message"),
    [
        ("get_market_bars", {"symbol": "EURUSD", "frequency": "day"}, "frequency"),
        ("get_market_bars", {"symbol": "EURUSD", "limit": 0}, "limit"),
        (
            "get_macro_observations",
            {"symbol": "EURUSD", "start_date": "2024-02-01", "end_date": "2024-01-01"},
            "start_date",
        ),
        ("get_news", {"symbol": "EURUSD", "start_date": "2024/01/01"}, "start_date"),
    ],
)
def test_reader_rejects_invalid_optional_filters(
    method: str, kwargs: dict[str, Any], message: str
) -> None:
    """错误筛选参数应在查询前给出稳定、可理解的错误。"""
    reader, _client = _reader()

    with pytest.raises(MarketDataReaderError, match=message):
        getattr(reader, method)(**kwargs)


def test_reader_reports_unknown_canonical_symbol() -> None:
    """未知标准代码不能退化成空列表，以免 Agent 把输入错误当作无数据。"""
    reader, client = _reader()
    client.instrument_rows = []

    with pytest.raises(MarketDataReaderError, match="未找到标准工具代码 'UNKNOWN'"):
        reader.get_latest_prices("unknown")
