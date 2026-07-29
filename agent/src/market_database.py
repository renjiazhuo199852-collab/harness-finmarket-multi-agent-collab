"""Optional, read-only PostgreSQL connection support for internal market data.

This module intentionally has no Agent Tool classes. It provides one narrow
connection boundary that the four market-data tools will share in a later PR.
The module never opens a network connection during import or configuration
loading, which keeps normal Vibe-Trading installations unchanged. PostgreSQL
role grants remain the authorization boundary; this client only enforces the
application's read-only behavior.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from src.config.accessor import get_env_config
from src.config.env_schema import MarketDatabaseConfig


class MarketDatabaseUnavailable(RuntimeError):
    """Raised when the optional market-data database cannot be used safely."""


ConnectionFactory = Callable[..., Any]


class MarketDatabaseClient:
    """Run internal, parameterized read queries against PostgreSQL.

    The caller must keep SQL statements in trusted Python source code and pass
    user-provided values only through ``params``. Every connection starts in a
    read-only transaction to prevent accidental writes by these tools. This is
    an application control, not a replacement for PostgreSQL role grants.

    Args:
        config: Explicit settings for tests, or the shared environment config
            when omitted.
        connect: Optional dependency-injected connection factory for unit tests.
            Production callers omit it and lazily import ``psycopg``.
    """

    def __init__(
        self,
        config: MarketDatabaseConfig | None = None,
        *,
        connect: ConnectionFactory | None = None,
    ) -> None:
        self._config = config or get_env_config().market_database
        self._connect = connect

    @property
    def is_configured(self) -> bool:
        """Return whether this client has an explicitly enabled complete configuration."""
        return self._config.is_configured()

    @contextmanager
    def read_only_connection(self) -> Iterator[Any]:
        """Yield one short-lived PostgreSQL connection in read-only mode.

        Returns:
            A live psycopg connection. It is always closed when the context
            exits, including on query failures.

        Raises:
            MarketDatabaseUnavailable: If configuration, driver import, or
                connection establishment fails.
        """
        if not self.is_configured:
            raise MarketDatabaseUnavailable(
                "market database is not configured; set MARKET_DB_ENABLED=true "
                "and all required MARKET_DB_* values"
            )

        connect = self._connect or self._load_psycopg_connect()
        try:
            # 连接参数只来自受控环境配置。只读事务和查询超时限制 Tool 的正常行为，
            # 但数据库账户本身的授权范围仍以 PostgreSQL 的 GRANT 为准。
            connection = connect(
                host=self._config.host,
                port=self._config.port,
                dbname=self._config.database,
                user=self._config.user,
                password=self._config.password,
                connect_timeout=self._config.connect_timeout_seconds,
                options=(
                    "-c default_transaction_read_only=on "
                    f"-c statement_timeout={self._config.statement_timeout_ms}"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - convert driver details to one stable domain error
            raise MarketDatabaseUnavailable(f"market database connection failed: {exc}") from exc

        try:
            yield connection
        finally:
            # 不提交事务；连接关闭时会回滚只读事务，避免把连接状态带入下一次 Tool 调用。
            connection.close()

    def fetch_all(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Execute one trusted parameterized SELECT and return structured row mappings.

        Args:
            query: A fixed SQL statement owned by application source code.
            params: Bound values for the statement. Never interpolate values
                into ``query`` with f-strings or string concatenation.

        Returns:
            Rows mapped from selected column names to native Python values.
            Tool-specific output code is responsible for JSON serialization.
        """
        with self.read_only_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, tuple(params))
                columns = [column.name for column in cursor.description or ()]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    @staticmethod
    def _load_psycopg_connect() -> ConnectionFactory:
        """Load psycopg only when a configured database call is actually made."""
        try:
            import psycopg
        except ImportError as exc:
            raise MarketDatabaseUnavailable(
                "PostgreSQL support is not installed; run pip install -e '.[market-db]'"
            ) from exc
        return psycopg.connect
