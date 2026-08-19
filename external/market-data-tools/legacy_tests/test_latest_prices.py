"""latest_prices 字段解析和只读查询适配器测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from latest_prices_adapter import query_latest_prices  # noqa: E402
from resolve_dataset_fields import (  # noqa: E402
    LATEST_PRICE_FIELDS,
    resolve_dataset_fields,
)


class FieldCursor:
    """为字段解析器提供目录记录和 information_schema 结果。"""

    def __init__(self, physical_columns: list[str] | None = None) -> None:
        self.rows: list[tuple[object, ...]] = []
        self.physical_columns = physical_columns or list(LATEST_PRICE_FIELDS)

    def execute(self, query: str, _parameters: tuple[object, ...]) -> None:
        if "FROM source.dataset_field_catalog" in query:
            self.rows = [
                ("LSEG_SPOT_PRICE.LAST", "LSEG_SPOT_PRICE", "LAST", "Last Price", "Latest", "numeric", "price"),
                ("LSEG_SPOT_PRICE.BID", "LSEG_SPOT_PRICE", "BID", "Bid Price", "Bid", "numeric", "price"),
                ("LSEG_SPOT_PRICE.ASK", "LSEG_SPOT_PRICE", "ASK", "Ask Price", "Ask", "numeric", "price"),
                ("LSEG_SPOT_PRICE.MID", "LSEG_SPOT_PRICE", "MID", "Mid Price", "Mid", "numeric", "price"),
                ("LSEG_SPOT_PRICE.PRICE_TIME", "LSEG_SPOT_PRICE", "PRICE_TIME", "PRICE Time", "Time", "timestamptz", ""),
            ]
        elif "FROM information_schema.columns" in query:
            self.rows = [(column,) for column in self.physical_columns]
        else:
            self.rows = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class PriceCursor:
    """捕获价格适配器生成的参数化查询，并返回一行报价。"""

    def __init__(self) -> None:
        self.parameters: tuple[object, ...] | None = None
        self.rows = [
            (
                datetime(2026, 8, 10, 3, 53, 45, tzinfo=timezone.utc),
                Decimal("1.152800"),
                Decimal("1.152700"),
                Decimal("1.152900"),
                Decimal("1.152800"),
            )
        ]

    def execute(self, _query: object, parameters: tuple[object, ...]) -> None:
        self.parameters = parameters

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def test_field_catalog_lowercase_matches_latest_prices_columns() -> None:
    """字段目录大写代码转小写后，应得到业务表真实列名。"""

    result = resolve_dataset_fields(
        FieldCursor(),
        "LSEG_SPOT_PRICE",
        "latest_prices",
        list(LATEST_PRICE_FIELDS),
    )

    assert result["status"] == "resolved"
    assert [field["field_name"] for field in result["fields"]] == list(LATEST_PRICE_FIELDS)
    assert [field["physical_column_name"] for field in result["fields"]] == list(LATEST_PRICE_FIELDS)
    assert result["missing_catalog_fields"] == []
    assert result["missing_physical_columns"] == []


def test_field_catalog_rejects_missing_physical_column() -> None:
    """目录声明了不存在的列时，不能继续生成业务表查询。"""

    result = resolve_dataset_fields(
        FieldCursor(physical_columns=["price_time", "last", "bid", "ask"]),
        "LSEG_SPOT_PRICE",
        "latest_prices",
        list(LATEST_PRICE_FIELDS),
    )

    assert result["status"] == "physical_column_missing"
    assert result["missing_physical_columns"] == ["mid"]


def test_latest_prices_adapter_uses_provider_identifier_and_preserves_decimal() -> None:
    """价格查询必须同时限定供应商和供应商标识，并保留十进制报价文本。"""

    cursor = PriceCursor()
    field_resolution = {
        "status": "resolved",
        "fields": [
            {"field_name": field, "physical_column_name": field, "business_name": field}
            for field in LATEST_PRICE_FIELDS
        ],
    }
    dataset_resolution = {
        "status": "resolved",
        "dataset_id": "LSEG_SPOT_PRICE",
        "storage_table_name": "latest_prices",
        "provider": "LSEG",
    }

    result = query_latest_prices(
        cursor,
        "FX_EURUSD",
        "LSEG",
        "EUR=",
        dataset_resolution,
        field_resolution,
    )

    assert cursor.parameters == ("LSEG", "EUR=", 1)
    assert result["status"] == "resolved"
    assert result["rows"][0]["last"] == "1.152800"
    assert result["rows"][0]["price_time"].endswith("+00:00")


def test_latest_prices_adapter_blocks_provider_mismatch() -> None:
    """目录供应商和标识供应商不一致时，适配器不能查询业务表。"""

    cursor = PriceCursor()
    result = query_latest_prices(
        cursor,
        "FX_EURUSD",
        "LSEG",
        "EUR=",
        {
            "status": "resolved",
            "dataset_id": "LSEG_SPOT_PRICE",
            "storage_table_name": "latest_prices",
            "provider": "OTHER",
        },
        {"status": "resolved", "fields": [{"field_name": "price_time", "physical_column_name": "price_time"}]},
    )

    assert result["status"] == "provider_mismatch"
    assert cursor.parameters is None
