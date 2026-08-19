"""金融工具主数据适配器。

本适配器只处理“把用户输入解析为标准金融工具”的查询，不查询价格、行情、
宏观观测或新闻事实。候选必须先经过 ``instrument_search_documents`` 检索和
``source.instrument_master`` 正式回查，适配器只负责按照字段目录读取最终结果。
"""

from __future__ import annotations

from typing import Any

from psycopg2 import sql


# 物理表名来自 source.dataset_catalog 的正式回查；这个常量只用于适配器注册表
# 的能力声明，不接受用户输入，也不接受大模型生成的表名。
INSTRUMENT_MASTER_TABLE = "instrument_master"

# 这是标准化路由对外返回的最小字段集合。字段仍然必须在
# source.dataset_field_catalog 中登记，并在 source.instrument_master 中存在。
INSTRUMENT_MASTER_FIELDS = (
    "instrument_id",
    "canonical_symbol",
    "name",
    "instrument_type",
    "status",
)


def query_instrument_master(
    cursor: Any,
    instrument_id: str,
    dataset_resolution: dict[str, Any],
    field_resolution: dict[str, Any],
    *,
    limit: int = 1,
) -> dict[str, Any]:
    """读取已经确认的金融工具，并返回标准 ``canonical_symbol``。

    动态列名通过 psycopg2 的 ``Identifier`` 构造，来源只能是已经通过字段目录和
    information_schema 校验的字段计划。这样既遵守字段目录约束，也避免把字段名
    拼接成不受控 SQL。查询条件中的 instrument_id 始终使用参数绑定。
    """

    if limit < 1 or limit > 100:
        raise ValueError("instrument_master limit 必须在 1 到 100 之间")
    if not instrument_id:
        return {
            "status": "rejected",
            "code": "INSTRUMENT_NOT_FOUND",
            "adapter": "instrument_master",
            "rows": [],
            "row_count": 0,
            "reason": "缺少已确认的 instrument_id",
        }

    fields = [
        str(field.get("physical_column_name"))
        for field in field_resolution.get("fields", [])
        if field.get("physical_column_name")
    ]
    if not fields:
        return {
            "status": "rejected",
            "code": "FIELD_RESOLUTION_FAILED",
            "adapter": "instrument_master",
            "rows": [],
            "row_count": 0,
            "reason": "instrument_master 没有可用的字段目录结果",
        }

    table_name = dataset_resolution.get("storage_table_name")
    if table_name != INSTRUMENT_MASTER_TABLE:
        return {
            "status": "rejected",
            "code": "DATASET_TABLE_MISMATCH",
            "adapter": "instrument_master",
            "rows": [],
            "row_count": 0,
            "reason": "数据集目录返回的物理表不是 instrument_master",
        }

    statement = sql.SQL(
        "SELECT {fields} FROM {schema}.{table} "
        "WHERE instrument_id = %s AND status = 'active' "
        "ORDER BY instrument_id LIMIT %s"
    ).format(
        fields=sql.SQL(", ").join(sql.Identifier(field) for field in fields),
        schema=sql.Identifier("source"),
        table=sql.Identifier(table_name),
    )
    cursor.execute(statement, (instrument_id, limit))
    rows = [
        {field: value for field, value in zip(fields, row)}
        for row in cursor.fetchall()
    ]
    return {
        "status": "resolved" if rows else "not_found",
        "adapter": "instrument_master",
        "dataset_id": dataset_resolution.get("dataset_id"),
        "storage_table_name": table_name,
        "fields": fields,
        "rows": rows,
        "row_count": len(rows),
        "reason": "已返回 active 金融工具标准记录" if rows else "未找到 active 金融工具",
    }
