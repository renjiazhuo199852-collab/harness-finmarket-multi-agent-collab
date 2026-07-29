"""Unit tests for the optional read-only PostgreSQL connection boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.config.env_schema import MarketDatabaseConfig
from src.market_database import MarketDatabaseClient, MarketDatabaseUnavailable


def _configured_database() -> MarketDatabaseConfig:
    """Return complete non-secret settings used only by these unit tests."""
    return MarketDatabaseConfig(
        enabled=True,
        host="127.0.0.1",
        port=15433,
        database="icbc_shared",
        user="icbc_collab",
        password="test-password",
        connect_timeout_seconds=5,
        statement_timeout_ms=10000,
    )


class _FakeCursor:
    """Small psycopg-like cursor that records parameterized query execution."""

    description = [SimpleNamespace(name="instrument_id"), SimpleNamespace(name="close")]

    def __init__(self) -> None:
        self.executed: tuple[str, tuple[Any, ...]] | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.executed = (query, params)

    def fetchall(self) -> list[tuple[str, float]]:
        return [("FX000001", 1.1034)]


class _FakeConnection:
    """Small connection double used to verify closure without a live database."""

    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True


def test_client_rejects_disabled_or_incomplete_configuration() -> None:
    """A default configuration must not initiate a network connection."""
    client = MarketDatabaseClient(MarketDatabaseConfig())

    with pytest.raises(MarketDatabaseUnavailable, match="not configured"):
        client.fetch_all("SELECT 1")


def test_fetch_all_binds_parameters_and_closes_connection() -> None:
    """Trusted SQL uses bound values and returns selected columns as mappings."""
    captured: dict[str, Any] = {}
    connection = _FakeConnection()

    def fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return connection

    client = MarketDatabaseClient(_configured_database(), connect=fake_connect)
    result = client.fetch_all(
        "SELECT instrument_id, close FROM market_bars WHERE instrument_id = %s",
        ("FX000001",),
    )

    assert result == [{"instrument_id": "FX000001", "close": 1.1034}]
    assert connection.cursor_instance.executed == (
        "SELECT instrument_id, close FROM market_bars WHERE instrument_id = %s",
        ("FX000001",),
    )
    assert connection.closed is True
    # 连接参数把正常 Tool 调用固定为只读；数据库账户本身的 GRANT 仍决定最终权限边界。
    assert captured["options"] == "-c default_transaction_read_only=on -c statement_timeout=10000"


def test_connection_errors_have_a_stable_domain_exception() -> None:
    """Driver errors are surfaced without exposing a raw traceback to a Tool caller."""

    def failing_connect(**_kwargs: Any) -> Any:
        raise OSError("connection refused")

    client = MarketDatabaseClient(_configured_database(), connect=failing_connect)

    with pytest.raises(MarketDatabaseUnavailable, match="market database connection failed"):
        client.fetch_all("SELECT 1")
