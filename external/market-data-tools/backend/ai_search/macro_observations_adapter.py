"""``source.macro_observations`` 的受控查询适配器。

当前只执行已经在 ``source.dataset_field_catalog`` 登记的 ``LSEG_MACRO`` 数据集。
利率和债券收益率的数据行虽然位于同一张物理表，但对应字段目录尚未登记，本适配器
不会借用 ``LSEG_MACRO`` 的目录记录绕过元数据边界。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg2 import sql


MACRO_TABLE = "macro_observations"
MACRO_DATASET_ID = "LSEG_MACRO"
MACRO_FIELDS = (
    "value",
    "previous_value",
    "forecast_value",
    "revised_value",
)

# 这些列是宏观路线固定的定位、时间和结果元数据，不允许用户或模型自由提交。
# 它们用于定位、过滤、排序和解释一条观测记录，不属于
# ``dataset_field_catalog`` 登记的宏观业务数值字段。
MACRO_METADATA_COLUMNS = (
    "id",
    "metric_id",
    "instrument_id",
    "release_time",
    "frequency",
    "source",
    "source_identifier",
    "country",
    "unit",
)


def _json_value(value: Any) -> Any:
    """将 PostgreSQL 日期、时间和数值转换成稳定的 JSON 值。"""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # 宏观数值保留数据库十进制文本，避免浮点转换改变原始精度。
        return str(value)
    return value


def query_macro_observations(
    cursor: Any,
    instrument_id: str,
    provider: str,
    identifier: str,
    dataset_resolution: dict[str, Any],
    field_resolution: dict[str, Any],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    frequency: str | None = None,
    limit: int = 1,
) -> dict[str, Any]:
    """查询已确认宏观指标的实际、前值、预测值和修订值。"""

    if not instrument_id or not provider or not identifier:
        raise ValueError("宏观查询缺少 instrument_id、provider 或 identifier")
    if limit < 1 or limit > 1000:
        raise ValueError("macro_observations limit 必须在 1 到 1000 之间")
    if start_date and end_date and start_date >= end_date:
        raise ValueError("宏观查询开始日期必须早于结束日期")

    if dataset_resolution.get("status") != "resolved":
        return {"status": "skipped", "rows": [], "reason": "数据集目录没有 resolved"}
    if dataset_resolution.get("dataset_id") != MACRO_DATASET_ID:
        return {
            "status": "unsupported_dataset",
            "rows": [],
            "dataset_id": dataset_resolution.get("dataset_id"),
            "reason": "当前宏观查询适配器只支持已经登记字段的 LSEG_MACRO",
        }
    if dataset_resolution.get("provider") != provider:
        return {
            "status": "provider_mismatch",
            "rows": [],
            "provider": provider,
            "dataset_provider": dataset_resolution.get("provider"),
            "reason": "dataset_catalog.provider 与 instrument_identifier.provider 不一致",
        }
    if dataset_resolution.get("storage_table_name") != MACRO_TABLE:
        return {
            "status": "unsupported_dataset",
            "rows": [],
            "storage_table_name": dataset_resolution.get("storage_table_name"),
            "reason": "LSEG_MACRO 没有指向 macro_observations",
        }
    if field_resolution.get("status") != "resolved":
        return {"status": "skipped", "rows": [], "reason": "字段目录没有 resolved"}

    field_by_name = {
        field.get("field_name"): field for field in field_resolution.get("fields", [])
    }
    missing_fields = [field for field in MACRO_FIELDS if field not in field_by_name]
    if missing_fields:
        return {
            "status": "invalid",
            "rows": [],
            "missing_fields": missing_fields,
            "reason": "字段计划缺少 LSEG_MACRO 宏观值字段",
        }

    select_columns = sql.SQL(", ").join(
        [sql.Identifier(column) for column in MACRO_METADATA_COLUMNS]
        + [
            sql.Identifier(field_by_name[field]["physical_column_name"])
            for field in MACRO_FIELDS
        ]
    )
    conditions = [
        sql.SQL("instrument_id = %s"),
        sql.SQL("source = %s"),
        sql.SQL("source_identifier = %s"),
    ]
    parameters: list[Any] = [instrument_id, provider, identifier]
    if start_date is not None and end_date is not None:
        # end_date 是左闭右开边界，覆盖结束日的全部 release_time。
        conditions.extend(
            [sql.SQL("release_time >= %s"), sql.SQL("release_time < %s")]
        )
        parameters.extend([start_date, end_date])
    if frequency:
        conditions.append(sql.SQL("frequency = %s"))
        parameters.append(frequency)

    statement = sql.SQL(
        "SELECT {columns} FROM {table} WHERE {conditions} "
        "ORDER BY release_time DESC, id DESC LIMIT %s"
    ).format(
        columns=select_columns,
        table=sql.Identifier("source", MACRO_TABLE),
        conditions=sql.SQL(" AND ").join(conditions),
    )
    parameters.append(limit)
    cursor.execute(statement, tuple(parameters))
    rows = cursor.fetchall()

    data_rows = []
    selected_columns = list(MACRO_METADATA_COLUMNS) + list(MACRO_FIELDS)
    for row in rows:
        values = {
            column: _json_value(value)
            for column, value in zip(selected_columns, row)
        }
        # 一条记录明确分成两部分：data 只保存字段目录确认的业务值，
        # metadata 保存指标身份、发布时间、来源和单位等上下文信息。
        data_rows.append(
            {
                "data": {field: values[field] for field in MACRO_FIELDS},
                "metadata": {
                    column: values[column]
                    for column in MACRO_METADATA_COLUMNS
                },
            }
        )

    return {
        "status": "resolved" if data_rows else "not_found",
        "instrument_id": instrument_id,
        "provider": provider,
        "identifier": identifier,
        "dataset_id": dataset_resolution.get("dataset_id"),
        "storage_table_name": dataset_resolution.get("storage_table_name"),
        "frequency": frequency,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "filters": {
            "instrument_id": instrument_id,
            "source": provider,
            "source_identifier": identifier,
            "frequency": frequency,
            "linked_rows_only": True,
        },
        "fields": field_resolution.get("fields", []),
        "rows": data_rows,
        "row_count": len(data_rows),
        "reason": (
            "已按正式 instrument_id 返回宏观数据"
            if data_rows
            else "没有找到已关联 instrument_id 的宏观记录"
        ),
    }
