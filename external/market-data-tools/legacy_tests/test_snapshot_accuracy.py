"""基于 db_export_0802.xlsx 的真实快照数据质量和测试基准。

这些测试不调用大模型，也不把查询实现重新计算一遍。它们只确认测试快照本身的
业务行数量、目录关系和新闻人工标注引用仍然成立，作为后续在线准确性报告的独立
标准答案来源。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


openpyxl = pytest.importorskip("openpyxl")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PROJECT_ROOT.parent / "docs" / "db_export_0802.xlsx"
NEWS_CASES_PATH = PROJECT_ROOT / "tests" / "fixtures" / "news_relevance_cases.json"

# 四条已实现路线实际允许返回的字段子集。目录中还可能存在暂不使用的扩展字段，
# 因此这里只验证路线需要的字段，不把未来扩展字段误判成当前链路必需字段。
ROUTE_REQUIRED_FIELDS = {
    "LSEG_SPOT_PRICE": {"PRICE_TIME", "LAST", "BID", "ASK", "MID"},
    "LSEG_MARKET_BARS": {"DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"},
    "LSEG_MACRO": {"VALUE", "PREVIOUS_VALUE", "FORECAST_VALUE", "REVISED_VALUE"},
    "LSEG_NEWS": {"TITLE", "SUMMARY", "CONTENT"},
}


def _read_actual_rows(sheet_name: str) -> list[dict[str, Any]]:
    """读取 Excel 中的真实业务行，排除导出文件附带的表信息和约束说明。"""

    if not SNAPSHOT_PATH.exists():
        pytest.skip(f"测试快照不存在：{SNAPSHOT_PATH}")
    workbook = openpyxl.load_workbook(SNAPSHOT_PATH, read_only=True, data_only=True)
    worksheet = workbook[sheet_name]
    rows = worksheet.iter_rows(values_only=True)
    headers = list(next(rows))
    actual_rows: list[dict[str, Any]] = []
    for row in rows:
        record = dict(zip(headers, row))
        # 四张业务表都使用数值 id 作为导出文件中的业务行识别条件；后续的
        # “表信息”“约束”等说明行不会有数值 id，因此不会进入测试标准答案。
        if isinstance(record.get("id"), (int, float)) and not isinstance(record.get("id"), bool):
            actual_rows.append(record)
    return actual_rows


def _read_catalog_rows(sheet_name: str, key: str) -> list[dict[str, Any]]:
    """读取目录表的真实行，过滤 Excel 导出附带的元数据行。"""

    if not SNAPSHOT_PATH.exists():
        pytest.skip(f"测试快照不存在：{SNAPSHOT_PATH}")
    workbook = openpyxl.load_workbook(SNAPSHOT_PATH, read_only=True, data_only=True)
    worksheet = workbook[sheet_name]
    rows = worksheet.iter_rows(values_only=True)
    headers = list(next(rows))
    return [
        dict(zip(headers, row))
        for row in rows
        if str(dict(zip(headers, row)).get(key) or "").startswith("LSEG_")
    ]


def test_snapshot_business_row_counts() -> None:
    """四张业务表的实际行数固定为本次准确性报告的基准。"""

    expected_counts = {
        "latest_prices": 15,
        "market_bars": 127,
        "macro_observations": 94,
        "news_articles": 76,
    }
    assert {
        sheet: len(_read_actual_rows(sheet))
        for sheet in expected_counts
    } == expected_counts


def test_snapshot_latest_prices_has_unique_source_keys() -> None:
    """最新价格快照中每个供应商标识只能有一条当前报价。"""

    rows = _read_actual_rows("latest_prices")
    keys = [(row["source"], row["source_identifier"]) for row in rows]
    assert len(keys) == len(set(keys))
    assert {row["source"] for row in rows} == {"LSEG"}


@pytest.mark.parametrize(
    ("sheet_name", "key_columns"),
    [
        ("market_bars", ("source", "source_identifier", "frequency", "date")),
        (
            "macro_observations",
            ("metric_id", "instrument_id", "release_time", "source", "source_identifier"),
        ),
        ("news_articles", ("article_id", "source")),
    ],
)
def test_snapshot_business_keys_are_unique(
    sheet_name: str,
    key_columns: tuple[str, ...],
) -> None:
    """历史行情、宏观观测和新闻源表不能出现重复业务键。"""

    rows = _read_actual_rows(sheet_name)
    keys = [tuple(row[column] for column in key_columns) for row in rows]
    assert len(keys) == len(set(keys))


def test_snapshot_market_bars_has_six_daily_series() -> None:
    """历史行情快照应包含六个独立的 LSEG 日线序列。"""

    rows = _read_actual_rows("market_bars")
    series = {
        (row["source"], row["source_identifier"], row["frequency"])
        for row in rows
    }
    assert len(series) == 6
    assert {row["frequency"] for row in rows} == {"daily"}
    assert {row["source"] for row in rows} == {"LSEG"}
    # 当前 FX 日线没有成交量，适配器必须保留数据库中的 NULL，不能凭空补零。
    assert all(row["volume"] is None for row in rows)


def test_snapshot_macro_observations_has_expected_value_boundaries() -> None:
    """宏观快照的值字段边界应与当前数据事实一致。"""

    rows = _read_actual_rows("macro_observations")
    assert len({(row["instrument_id"], row["frequency"]) for row in rows}) == 73
    assert {row["frequency"] for row in rows} == {"daily", "monthly", "quarterly"}
    assert {row["unit"] for row in rows} == {"%", "index", "USD", "K"}
    assert all(row["value"] is not None for row in rows)
    # 0802 快照尚未提供前值、预测值和修订值；报告必须把它们显示为 NULL，不能
    # 把字段目录中的 unit 或模型推断当成业务表真实值。
    assert all(row[field] is None for row in rows for field in (
        "previous_value",
        "forecast_value",
        "revised_value",
    ))


def test_snapshot_catalog_routes_point_to_four_business_tables() -> None:
    """数据集目录必须把四条路线指向各自的物理业务表。"""

    rows = _read_catalog_rows("dataset_catalog", "dataset_id")
    routes = {
        row["dataset_id"]: row["storage_table_name"]
        for row in rows
        if row["dataset_id"] in {
            "LSEG_SPOT_PRICE",
            "LSEG_MARKET_BARS",
            "LSEG_MACRO",
            "LSEG_NEWS",
        }
    }
    assert routes == {
        "LSEG_SPOT_PRICE": "latest_prices",
        "LSEG_MARKET_BARS": "market_bars",
        "LSEG_MACRO": "macro_observations",
        "LSEG_NEWS": "news_articles",
    }


def test_snapshot_field_catalog_covers_supported_route_fields() -> None:
    """字段目录应覆盖四条路线实际会读取的字段，并能映射到物理列名。"""

    catalog_rows = _read_catalog_rows("dataset_field_catalog", "field_id")
    actual_fields: dict[str, set[str]] = {}
    for row in catalog_rows:
        actual_fields.setdefault(row["dataset_id"], set()).add(row["field_name"])

    physical_tables = {
        "LSEG_SPOT_PRICE": "latest_prices",
        "LSEG_MARKET_BARS": "market_bars",
        "LSEG_MACRO": "macro_observations",
        "LSEG_NEWS": "news_articles",
    }
    for dataset_id, required_fields in ROUTE_REQUIRED_FIELDS.items():
        assert required_fields <= actual_fields.get(dataset_id, set())
        physical_columns = set(_read_actual_rows(physical_tables[dataset_id])[0])
        assert {field.lower() for field in required_fields} <= physical_columns


def test_news_manual_labels_reference_existing_articles() -> None:
    """新闻人工标注只能引用快照中真实存在的 article_id。"""

    rows = _read_actual_rows("news_articles")
    article_ids = {row["article_id"] for row in rows}
    cases = json.loads(NEWS_CASES_PATH.read_text(encoding="utf-8"))
    assert cases
    for case in cases:
        referenced = set(case["must_include_article_ids"]) | set(case["must_not_include_article_ids"])
        assert referenced <= article_ids, case["case_id"]
