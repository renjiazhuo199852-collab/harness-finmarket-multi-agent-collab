"""按已经确认的 ``dataset_id`` 解析字段目录。

字段目录和金融工具、数据集目录的检索职责不同：

* ``dataset_id`` 已经由前面的数据集阶段确认，因此这里不做全库 Embedding 或模糊检索；
* 程序直接读取 ``source.dataset_field_catalog`` 中属于该数据集的字段；
* 当前源表的 ``field_name`` 是大写逻辑字段，程序统一转成小写后与 PostgreSQL
  业务表列名比较；
* 最终返回的是受控字段计划，后续适配器只能使用通过目录和物理列校验的字段。

本模块不调用对话大模型，也不修改 source 表中的原始 ``field_name``。
"""

from __future__ import annotations

from typing import Any


# latest_prices 路线的默认返回字段由业务路线固定，不由模型自由生成。
LATEST_PRICE_FIELDS = (
    "price_time",
    "last",
    "bid",
    "ask",
    "mid",
)


def _text(value: Any) -> str:
    """将目录字段转换为稳定的字符串；NULL 说明字段没有单位或描述。"""

    return "" if value is None else str(value).strip()


def resolve_dataset_fields(
    cursor: Any,
    dataset_id: str,
    storage_table_name: str,
    requested_fields: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """读取并校验一个数据集的字段计划。

    ``requested_fields`` 来自路线级业务规则，例如 latest_prices 的五个报价字段，
    而不是来自用户或模型直接提交的任意列名。即使调用方传入了大写字段，本函数
    也只做大小写转换，不做拼写纠错或字段猜测。
    """

    if not dataset_id:
        return {
            "status": "invalid",
            "dataset_id": dataset_id,
            "storage_table_name": storage_table_name,
            "requested_fields": list(requested_fields),
            "fields": [],
            "available_fields": [],
            "missing_catalog_fields": list(requested_fields),
            "missing_physical_columns": [],
            "reason": "缺少 dataset_id",
        }
    if not storage_table_name:
        return {
            "status": "invalid",
            "dataset_id": dataset_id,
            "storage_table_name": storage_table_name,
            "requested_fields": list(requested_fields),
            "fields": [],
            "available_fields": [],
            "missing_catalog_fields": [],
            "missing_physical_columns": [],
            "reason": "缺少 storage_table_name",
        }

    # 保持调用方声明的字段顺序，同时去掉重复项，确保 SELECT 列顺序稳定。
    requested = list(dict.fromkeys(_text(field).lower() for field in requested_fields if _text(field)))
    cursor.execute(
        """
        SELECT field_id,
               dataset_id,
               field_name,
               business_name,
               description,
               data_type,
               unit
        FROM source.dataset_field_catalog
        WHERE dataset_id = %s
        ORDER BY field_id
        """,
        (dataset_id,),
    )
    catalog_rows = cursor.fetchall()
    catalog_by_name = {
        _text(row[2]).lower(): {
            "field_id": row[0],
            "dataset_id": row[1],
            # field_name 是逻辑字段；对外字段计划统一使用小写。
            "field_name": _text(row[2]).lower(),
            "catalog_field_name": _text(row[2]),
            "business_name": _text(row[3]),
            "description": _text(row[4]),
            "data_type": _text(row[5]),
            "unit": _text(row[6]),
        }
        for row in catalog_rows
    }
    available_fields = sorted(catalog_by_name)
    missing_catalog_fields = [field for field in requested if field not in catalog_by_name]

    # 字段目录只声明了业务字段，还需要用 information_schema 验证小写列确实存在。
    # table 名来自已经回查过的 dataset_catalog，只作为参数参与元数据查询。
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'source'
          AND table_name = %s
          AND column_name = ANY(%s)
        """,
        (storage_table_name, requested),
    )
    physical_columns = {str(row[0]).lower() for row in cursor.fetchall()}

    fields: list[dict[str, Any]] = []
    missing_physical_columns: list[str] = []
    for field_name in requested:
        catalog_field = catalog_by_name.get(field_name)
        if catalog_field is None:
            continue
        if field_name not in physical_columns:
            missing_physical_columns.append(field_name)
            continue
        fields.append(
            {
                **catalog_field,
                "physical_column_name": field_name,
            }
        )

    if missing_catalog_fields:
        status = "catalog_field_missing"
        reason = "部分请求字段未登记在 dataset_field_catalog"
    elif missing_physical_columns:
        status = "physical_column_missing"
        reason = "部分字段目录记录未对应 source 业务表物理列"
    else:
        status = "resolved"
        reason = "字段目录和 source 物理列均已确认"

    return {
        "status": status,
        "dataset_id": dataset_id,
        "storage_table_name": storage_table_name,
        "requested_fields": requested,
        "fields": fields,
        "available_fields": available_fields,
        "missing_catalog_fields": missing_catalog_fields,
        "missing_physical_columns": missing_physical_columns,
        "reason": reason,
    }
