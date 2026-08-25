"""``source.market_bars`` 的受控日线/小时查询适配器。

适配器只接受前序已经确认的数据集、字段和供应商标识。目录仍登记一个
``LSEG_MARKET_BARS`` 日线数据集，但物理表同时保存 ``daily`` 和 ``hourly`` 原始
行；4H 请求只读取小时行，不在这里伪造或聚合指标。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg2 import sql


MARKET_BARS_TABLE = "market_bars"
MARKET_BARS_FREQUENCY = "daily"
MARKET_BARS_SUPPORTED_FREQUENCIES = ("daily", "hourly")
INTRADAY_TIME_COLUMN = "bar_time"
MARKET_BAR_FIELDS = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


def _json_value(value: Any) -> Any:
    """把 PostgreSQL 日期和数值转换成稳定的 JSON 值。"""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # OHLCV 数值保留数据库文本，避免浮点转换造成尾差。
        return str(value)
    return value


def query_market_bars(
    cursor: Any,
    instrument_id: str,
    provider: str,
    identifier: str,
    dataset_resolution: dict[str, Any],
    field_resolution: dict[str, Any],
    start_date: date,
    end_date: date,
    frequency: str = MARKET_BARS_FREQUENCY,
    limit: int = 100,
) -> dict[str, Any]:
    """按工具标识和日期范围查询日线 OHLCV 数据。"""

    if limit < 1 or limit > 1000:
        raise ValueError("market_bars limit 必须在 1 到 1000 之间")
    if start_date > end_date:
        raise ValueError("market_bars 开始日期不能晚于结束日期")

    storage_table_name = dataset_resolution.get("storage_table_name")
    dataset_provider = dataset_resolution.get("provider")
    dataset_frequency = dataset_resolution.get("frequency")
    if dataset_resolution.get("status") != "resolved":
        return {"status": "skipped", "rows": [], "reason": "数据集目录没有 resolved"}
    if dataset_provider != provider:
        return {
            "status": "provider_mismatch",
            "rows": [],
            "provider": provider,
            "dataset_provider": dataset_provider,
            "reason": "dataset_catalog.provider 与 instrument_identifier.provider 不一致",
        }
    if storage_table_name != MARKET_BARS_TABLE:
        return {
            "status": "unsupported_dataset",
            "rows": [],
            "storage_table_name": storage_table_name,
            "reason": "market_bars 路线确认的数据集没有指向 market_bars",
        }
    if (
        dataset_frequency != MARKET_BARS_FREQUENCY
        or frequency not in MARKET_BARS_SUPPORTED_FREQUENCIES
    ):
        return {
            "status": "unsupported_frequency",
            "rows": [],
            "frequency": frequency,
            "dataset_frequency": dataset_frequency,
            "reason": "当前 market_bars 只支持 daily 或 hourly 原始数据",
        }
    if field_resolution.get("status") != "resolved":
        return {"status": "skipped", "rows": [], "reason": "字段目录没有 resolved"}

    fields = field_resolution.get("fields") or []
    if not fields:
        return {"status": "invalid", "rows": [], "reason": "字段计划为空"}
    field_by_name = {field.get("field_name"): field for field in fields}
    missing_fields = [field for field in MARKET_BAR_FIELDS if field not in field_by_name]
    if missing_fields:
        return {
            "status": "invalid",
            "rows": [],
            "missing_fields": missing_fields,
            "reason": "字段计划缺少日线 OHLCV 字段",
        }

    selected_fields = list(MARKET_BAR_FIELDS)
    if frequency == "hourly":
        # ``bar_time`` is part of the fixed source.market_bars physical contract,
        # but is intentionally not exposed as a user/model-selectable catalog field.
        selected_fields.append(INTRADAY_TIME_COLUMN)
    select_columns = sql.SQL(", ").join(
        sql.Identifier(
            field_by_name[field]["physical_column_name"]
            if field in field_by_name
            else INTRADAY_TIME_COLUMN
        )
        for field in selected_fields
    )
    date_column_name = (
        INTRADAY_TIME_COLUMN
        if frequency == "hourly"
        else field_by_name["date"]["physical_column_name"]
    )
    end_bound = end_date + timedelta(days=1)
    date_predicate = (
        sql.SQL("{date_column} >= %s AND {date_column} < %s").format(
            date_column=sql.Identifier(date_column_name)
        )
        if frequency == "hourly"
        else sql.SQL("{date_column} >= %s AND {date_column} <= %s").format(
            date_column=sql.Identifier(date_column_name)
        )
    )
    statement = sql.SQL(
        "SELECT {columns} "
        "FROM {table} "
        "WHERE source = %s "
        "AND source_identifier = %s "
        "AND frequency = %s "
        "AND {date_predicate} "
        "ORDER BY {date_column} ASC "
        "LIMIT %s"
    ).format(
        columns=select_columns,
        table=sql.Identifier("source", storage_table_name),
        date_column=sql.Identifier(date_column_name),
        date_predicate=date_predicate,
    )
    end_parameter = end_bound if frequency == "hourly" else end_date
    cursor.execute(
        statement,
        (provider, identifier, frequency, start_date, end_parameter, limit),
    )
    rows = cursor.fetchall()
    data_rows = []
    for row in rows:
        values = {
            field_name: _json_value(value)
            for field_name, value in zip(MARKET_BAR_FIELDS, row[: len(MARKET_BAR_FIELDS)])
        }
        values["frequency"] = frequency
        if frequency == "hourly":
            values["bar_time"] = _json_value(row[-1])
        data_rows.append(values)
    return {
        "status": "resolved" if data_rows else "not_found",
        "instrument_id": instrument_id,
        "provider": provider,
        "identifier": identifier,
        "dataset_id": dataset_resolution.get("dataset_id"),
        "storage_table_name": storage_table_name,
        "frequency": frequency,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "filters": {
            "source": provider,
            "source_identifier": identifier,
            "frequency": frequency,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "fields": field_resolution.get("fields", []),
        "rows": data_rows,
        "row_count": len(data_rows),
        "reason": (
            f"已按 bar_time 升序返回 {frequency} OHLCV"
            if data_rows
            else f"指定日期范围没有匹配的 {frequency} 记录"
        ),
    }
