"""Hourly raw-bar request and adapter contract tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

from backend.ai_search.market_bar_request import parse_market_bar_request
from backend.ai_search.market_bars_adapter import query_market_bars


def test_four_hour_request_resolves_to_hourly_raw_frequency() -> None:
    result = parse_market_bar_request(
        "EURUSD 4H K线 最近两周",
        reference_date=date(2026, 8, 25),
    )

    assert result["status"] == "resolved"
    assert result["frequency"] == "hourly"
    assert result["period_type"] == "hourly"


class _Cursor:
    def __init__(self) -> None:
        self.executed = None

    def execute(self, statement, params) -> None:
        self.executed = (statement, params)

    def fetchall(self):
        return [
            (
                date(2026, 8, 24),
                "1.10",
                "1.11",
                "1.09",
                "1.105",
                "0",
                datetime(2026, 8, 24, 4, tzinfo=timezone.utc),
            )
        ]


def test_hourly_adapter_returns_bar_time_and_frequency() -> None:
    cursor = _Cursor()
    field_resolution = {
        "status": "resolved",
        "fields": [
            {"field_name": name, "physical_column_name": name}
            for name in ("date", "open", "high", "low", "close", "volume")
        ],
    }
    result = query_market_bars(
        cursor,
        "FX_EURUSD",
        "LSEG",
        "EUR=",
        {
            "status": "resolved",
            "dataset_id": "LSEG_MARKET_BARS",
            "storage_table_name": "market_bars",
            "provider": "LSEG",
            "frequency": "daily",
        },
        field_resolution,
        date(2026, 8, 24),
        date(2026, 8, 25),
        frequency="hourly",
        limit=10,
    )

    assert result["status"] == "resolved"
    assert result["frequency"] == "hourly"
    assert result["rows"][0]["frequency"] == "hourly"
    assert result["rows"][0]["bar_time"].startswith("2026-08-24T04:00:00")
    assert cursor.executed is not None
