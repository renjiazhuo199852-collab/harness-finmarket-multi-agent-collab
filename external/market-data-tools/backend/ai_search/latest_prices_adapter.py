"""``source.latest_prices`` 的受控只读查询适配器。

适配器只接受前序已经确认的对象：

* ``instrument_identifier`` 提供供应商和供应商标识；
* ``dataset_catalog`` 提供正式 ``dataset_id`` 和 ``storage_table_name``；
* 字段解析器提供已经通过目录与物理列校验的字段计划。

它使用 ``psycopg2.sql.Identifier`` 组合表名和列名，不接受大模型生成的 SQL，也不
把自然语言直接拼接进 SQL。金融数值转换成字符串，避免浮点化损失报价精度。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg2 import sql


LATEST_PRICES_TABLE = "latest_prices"


def _json_value(value: Any) -> Any:
    """把 PostgreSQL 结果转换为可稳定 JSON 化的值。"""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # 价格保留数据库的十进制文本，避免转换为 binary float 后出现尾差。
        return str(value)
    return value


def query_latest_prices(
    cursor: Any,
    instrument_id: str,
    provider: str,
    identifier: str,
    dataset_resolution: dict[str, Any],
    field_resolution: dict[str, Any],
    limit: int = 1,
) -> dict[str, Any]:
    """按供应商标识查询 ``latest_prices`` 最新报价。"""

    if limit < 1 or limit > 10:
        raise ValueError("latest_prices limit 必须在 1 到 10 之间")

    dataset_provider = dataset_resolution.get("provider")
    storage_table_name = dataset_resolution.get("storage_table_name")
    if dataset_resolution.get("status") != "resolved":
        return {
            "status": "skipped",
            "reason": "数据集目录没有 resolved，禁止查询业务表",
            "rows": [],
        }
    if dataset_provider != provider:
        return {
            "status": "provider_mismatch",
            "reason": "dataset_catalog.provider 与 instrument_identifier.provider 不一致",
            "rows": [],
            "provider": provider,
            "dataset_provider": dataset_provider,
        }
    if storage_table_name != LATEST_PRICES_TABLE:
        return {
            "status": "unsupported_dataset",
            "reason": "latest_prices 路线确认的数据集没有指向 latest_prices",
            "rows": [],
            "storage_table_name": storage_table_name,
        }
    if field_resolution.get("status") != "resolved":
        return {
            "status": "skipped",
            "reason": "字段目录没有 resolved，禁止查询业务表",
            "rows": [],
        }

    fields = field_resolution.get("fields") or []
    if not fields:
        return {
            "status": "invalid",
            "reason": "字段计划为空",
            "rows": [],
        }
    time_field = next(
        (field for field in fields if field.get("field_name") == "price_time"),
        None,
    )
    if time_field is None:
        return {
            "status": "invalid",
            "reason": "字段计划缺少 price_time，无法确定最新记录",
            "rows": [],
        }

    physical_columns = [field["physical_column_name"] for field in fields]
    select_columns = sql.SQL(", ").join(
        sql.Identifier(column) for column in physical_columns
    )
    statement = sql.SQL(
        "SELECT {columns} "
        "FROM {table} "
        "WHERE source = %s AND source_identifier = %s "
        "ORDER BY {time_column} DESC "
        "LIMIT %s"
    ).format(
        columns=select_columns,
        table=sql.Identifier("source", storage_table_name),
        time_column=sql.Identifier(time_field["physical_column_name"]),
    )
    cursor.execute(statement, (provider, identifier, limit))
    rows = cursor.fetchall()
    field_names = [field["field_name"] for field in fields]
    data_rows = [
        {
            field_name: _json_value(value)
            for field_name, value in zip(field_names, row)
        }
        for row in rows
    ]
    return {
        "status": "resolved" if data_rows else "not_found",
        "instrument_id": instrument_id,
        "provider": provider,
        "identifier": identifier,
        "dataset_id": dataset_resolution.get("dataset_id"),
        "storage_table_name": storage_table_name,
        "filters": {
            "source": provider,
            "source_identifier": identifier,
        },
        "fields": field_resolution.get("fields", []),
        "rows": data_rows,
        "row_count": len(data_rows),
        "reason": "已按 price_time 倒序返回最新报价" if data_rows else "没有匹配的最新价格记录",
    }
