"""真实 SDK / PostgreSQL 调用的可观测事件契约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.config.env_schema import MarketDatabaseConfig
from src.market_data_reader import MarketDataReader
from src.market_database import MarketDatabaseClient
from src.observability import observation_scope


class _ReaderClient:
    is_configured = True

    def fetch_all(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        del params
        if "instrument_master" in query:
            return [
                {
                    "instrument_id": "FX000001",
                    "canonical_symbol": "EURUSD",
                    "name": "EUR/USD Spot",
                    "instrument_type": "FX",
                    "country": "EU",
                    "region": "Europe",
                    "currency": "USD",
                    "status": "active",
                }
            ]
        return [{"bid": 1.1032, "ask": 1.1036, "source": "LSEG"}]


class _Cursor:
    description = [SimpleNamespace(name="instrument_id"), SimpleNamespace(name="close")]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self):
        return [("FX000001", 1.1034)]


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()

    def cursor(self):
        return self.cursor_instance

    def close(self) -> None:
        return None


def test_reader_emits_detailed_sdk_input_and_output() -> None:
    events: list[dict] = []

    with observation_scope(events.append):
        payload = MarketDataReader(_ReaderClient()).get_latest_prices(
            "EURUSD",
            source="LSEG",
        )

    assert payload["count"] == 1
    assert [event["type"] for event in events] == [
        "sdk_call_started",
        "sdk_call_completed",
    ]
    assert events[0]["data"]["operation"] == "MarketDataReader.get_latest_prices"
    assert events[0]["data"]["input"] == {"symbol": "EURUSD", "source": "LSEG"}
    assert events[1]["data"]["output"]["count"] == 1
    assert events[1]["data"]["elapsed_ms"] >= 0


def test_database_emits_parameterized_sql_and_row_output_without_password() -> None:
    events: list[dict] = []
    config = MarketDatabaseConfig(
        enabled=True,
        host="127.0.0.1",
        port=15433,
        database="icbc_shared",
        user="icbc_collab",
        password="must-not-appear",
    )
    client = MarketDatabaseClient(config, connect=lambda **_kwargs: _Connection())

    with observation_scope(events.append):
        rows = client.fetch_all(
            "SELECT instrument_id, close FROM public.market_bars "
            "WHERE instrument_id = %s",
            ("FX000001",),
        )

    assert rows == [{"instrument_id": "FX000001", "close": 1.1034}]
    assert [event["type"] for event in events] == [
        "database_query_started",
        "database_query_completed",
    ]
    assert events[0]["data"]["input"]["params"] == ["FX000001"]
    assert "public.market_bars" in events[0]["data"]["input"]["sql"]
    assert events[1]["data"]["output"]["row_count"] == 1
    assert "must-not-appear" not in str(events)
