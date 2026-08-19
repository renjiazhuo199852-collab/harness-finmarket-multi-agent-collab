"""生成四条 AI Search 查询路线的准确性测试报告。

报告使用两类独立证据：

1. ``db_export_0802.xlsx`` 作为冻结快照标准答案，检查四张 source 业务表和目录关系；
2. 可选的真实 HTTP 查询，检查当前模型、检索表和业务适配器的端到端结果。

新闻没有稳定的金融工具外键，因此新闻准确性使用
``tests/fixtures/news_relevance_cases.json`` 中的人工标注样本判断。脚本只读数据库，
不会建表、写表或修改 source 数据。
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

import openpyxl

from .env_config import load_project_env


load_project_env()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = PROJECT_ROOT.parent / "docs" / "db_export_0802.xlsx"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "accuracy_test_report.md"
NEWS_CASES = PROJECT_ROOT / "tests" / "fixtures" / "news_relevance_cases.json"

BUSINESS_SHEETS = (
    "latest_prices",
    "market_bars",
    "macro_observations",
    "news_articles",
)

ROUTE_REQUIRED_FIELDS = {
    "LSEG_SPOT_PRICE": {"PRICE_TIME", "LAST", "BID", "ASK", "MID"},
    "LSEG_MARKET_BARS": {"DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"},
    "LSEG_MACRO": {"VALUE", "PREVIOUS_VALUE", "FORECAST_VALUE", "REVISED_VALUE"},
    "LSEG_NEWS": {"TITLE", "SUMMARY", "CONTENT"},
}

ROUTE_TABLES = {
    "LSEG_SPOT_PRICE": "latest_prices",
    "LSEG_MARKET_BARS": "market_bars",
    "LSEG_MACRO": "macro_observations",
    "LSEG_NEWS": "news_articles",
}

DB_COLUMNS = {
    "latest_prices": (
        "source",
        "source_identifier",
        "price_time",
        "last",
        "bid",
        "ask",
        "mid",
    ),
    "market_bars": (
        "source",
        "source_identifier",
        "frequency",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ),
    "macro_observations": (
        "metric_id",
        "instrument_id",
        "release_time",
        "frequency",
        "value",
        "previous_value",
        "forecast_value",
        "revised_value",
        "source",
        "source_identifier",
        "country",
        "unit",
    ),
    "news_articles": (
        "article_id",
        "source",
        "publish_time",
        "title",
        "language",
        "content",
        "summary",
        "related_entities",
        "keywords",
    ),
}

NUMERIC_COLUMNS = {
    "last",
    "bid",
    "ask",
    "mid",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "previous_value",
    "forecast_value",
    "revised_value",
}

LIVE_CASES = (
    {
        "case_id": "latest_eurusd",
        "route": "latest_prices",
        "query": "查询 EURUSD 的最新价格",
        "kind": "latest",
        "identifier": "EUR=",
        "instrument_id": "FX_EURUSD",
    },
    {
        "case_id": "latest_usdjpy",
        "route": "latest_prices",
        "query": "查询 USDJPY 当前报价",
        "kind": "latest",
        "identifier": "JPY=",
        "instrument_id": "FX_USDJPY",
    },
    {
        "case_id": "latest_gold",
        "route": "latest_prices",
        "query": "查询 XAU= 的最新价格",
        "kind": "latest",
        "identifier": "XAU=",
        "instrument_id": "CMD_GOLD",
    },
    {
        "case_id": "bars_eurusd_full_range",
        "route": "market_bars",
        "query": "查询 EURUSD 2026-07-01 到 2026-07-31 的日K线",
        "kind": "market_bars",
        "identifier": "EUR=",
        "instrument_id": "FX_EURUSD",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
    },
    {
        "case_id": "bars_usdjpy_full_range",
        "route": "market_bars",
        "query": "查询 USDJPY 2026-07-01 到 2026-07-31 的日线行情",
        "kind": "market_bars",
        "identifier": "JPY=",
        "instrument_id": "FX_USDJPY",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
    },
    {
        "case_id": "bars_unsupported_monthly",
        "route": "market_bars",
        "query": "查询 EURUSD 月K线",
        "kind": "market_bars_unsupported",
    },
    {
        "case_id": "macro_us_cpi",
        "route": "macro_observations",
        "query": "查询美国 CPI 最新值",
        "kind": "macro",
        "instrument_id": "METRIC_US_CPI_YOY",
    },
    {
        "case_id": "macro_us_core_cpi",
        "route": "macro_observations",
        "query": "查询美国核心 CPI 最新值",
        "kind": "macro",
        "instrument_id": "METRIC_US_CORE_CPI_YOY",
    },
    {
        "case_id": "macro_eu_pmi",
        "route": "macro_observations",
        "query": "查询欧元区制造业 PMI 最近一年月度实际值",
        "kind": "macro",
        "instrument_id": "METRIC_EU_PMI_MANUFACTURING",
    },
    {
        "case_id": "macro_interest_rate_boundary",
        "route": "macro_observations",
        "query": "查询美国联邦基金利率最新值",
        "kind": "macro_unsupported",
    },
)


def _is_business_row(record: dict[str, Any], sheet_name: str) -> bool:
    """判断一行是否是真实业务数据，而不是导出文件中的说明行。"""

    if sheet_name in BUSINESS_SHEETS:
        return isinstance(record.get("id"), (int, float)) and not isinstance(
            record.get("id"), bool
        )
    if sheet_name == "instrument_master":
        return bool(record.get("instrument_id") and record.get("instrument_type"))
    if sheet_name == "instrument_identifier":
        return bool(
            record.get("instrument_id")
            and record.get("provider")
            and record.get("identifier")
            and record.get("identifier_type")
        )
    if sheet_name == "dataset_catalog":
        return str(record.get("dataset_id") or "").startswith("LSEG_")
    if sheet_name == "dataset_field_catalog":
        return str(record.get("field_id") or "").startswith("LSEG_")
    return False


def read_snapshot(path: Path) -> dict[str, list[dict[str, Any]]]:
    """读取准确性报告所需的快照业务行。"""

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    result: dict[str, list[dict[str, Any]]] = {}
    for sheet_name in (
        *BUSINESS_SHEETS,
        "instrument_master",
        "instrument_identifier",
        "dataset_catalog",
        "dataset_field_catalog",
    ):
        worksheet = workbook[sheet_name]
        rows = worksheet.iter_rows(values_only=True)
        headers = list(next(rows))
        result[sheet_name] = [
            record
            for row in rows
            for record in [dict(zip(headers, row))]
            if _is_business_row(record, sheet_name)
        ]
    workbook.close()
    return result


def canonical_value(
    value: Any,
    *,
    json_column: bool = False,
    numeric: bool = False,
) -> Any:
    """把 Excel 和 PostgreSQL 返回值转成可以独立比较的形式。"""

    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        value = str(value)
    if numeric:
        try:
            # Excel 快照常保存 6 位小数，PostgreSQL numeric 可能返回 10 位小数；
            # 准确性比较应比较数值本身，而不是无业务意义的尾随零。
            return format(Decimal(str(value)).normalize(), "f")
        except (ValueError, ArithmeticError):
            return str(value)
    if json_column:
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        except (TypeError, json.JSONDecodeError):
            return str(value)
    return str(value)


def row_counter(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> Counter[tuple[Any, ...]]:
    """按业务字段生成多重集合，能同时发现缺行、增行和重复行。"""

    json_columns = {"related_entities", "keywords"}
    return Counter(
        tuple(
            canonical_value(
                row.get(column),
                json_column=column in json_columns,
                numeric=column in NUMERIC_COLUMNS,
            )
            for column in columns
        )
        for row in rows
    )


def database_kwargs() -> dict[str, Any]:
    """读取只读报告连接配置，不在报告或输出中暴露数据库密码。"""

    password = os.getenv("AI_SEARCH_DB_PASSWORD") or os.getenv("LOCAL_PG_PASSWORD")
    if not password:
        raise RuntimeError("未配置 AI_SEARCH_DB_PASSWORD 或 LOCAL_PG_PASSWORD")
    return {
        "host": os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("AI_SEARCH_DB_PORT", "15433")),
        "dbname": os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"),
        "user": os.getenv("AI_SEARCH_DB_USER", "icbc_collab"),
        "password": password,
        "connect_timeout": 10,
    }


def read_database_snapshot() -> dict[str, list[dict[str, Any]]]:
    """只读读取四张 source 业务表，供快照一致性比较。"""

    import psycopg2

    result: dict[str, list[dict[str, Any]]] = {}
    with psycopg2.connect(**database_kwargs()) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            for table, columns in DB_COLUMNS.items():
                cursor.execute(
                    f"SELECT {', '.join(columns)} FROM source.{table}"
                )
                result[table] = [
                    dict(zip(columns, row)) for row in cursor.fetchall()
                ]
    return result


def check_snapshot(snapshot: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """检查快照数量、业务关系和新闻人工标注引用。"""

    checks: list[dict[str, Any]] = []

    def add(name: str, expected: Any, actual: Any, passed: bool, detail: str = "") -> None:
        checks.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "passed": passed,
                "detail": detail,
            }
        )

    expected_counts = {
        "latest_prices": 15,
        "market_bars": 127,
        "macro_observations": 94,
        "news_articles": 76,
    }
    for table, expected in expected_counts.items():
        actual = len(snapshot[table])
        add(f"快照行数 {table}", expected, actual, actual == expected)

    latest_keys = [
        (row.get("source"), row.get("source_identifier"))
        for row in snapshot["latest_prices"]
    ]
    add(
        "latest_prices 业务键唯一",
        len(latest_keys),
        len(set(latest_keys)),
        len(latest_keys) == len(set(latest_keys)),
    )

    duplicate_key_specs = {
        "market_bars": ("source", "source_identifier", "frequency", "date"),
        "macro_observations": (
            "metric_id",
            "instrument_id",
            "release_time",
            "source",
            "source_identifier",
        ),
        "news_articles": ("article_id", "source"),
    }
    for table, key_columns in duplicate_key_specs.items():
        keys = [tuple(row.get(column) for column in key_columns) for row in snapshot[table]]
        duplicate_count = len(keys) - len(set(keys))
        add(
            f"{table} 业务键重复数",
            0,
            duplicate_count,
            duplicate_count == 0,
            f"key_columns={key_columns}",
        )

    market_series = {
        (row.get("source"), row.get("source_identifier"), row.get("frequency"))
        for row in snapshot["market_bars"]
    }
    add("market_bars 日线序列数量", 6, len(market_series), len(market_series) == 6)
    add(
        "market_bars 频率",
        ["daily"],
        sorted({row.get("frequency") for row in snapshot["market_bars"]}),
        {row.get("frequency") for row in snapshot["market_bars"]} == {"daily"},
    )

    macro_rows = snapshot["macro_observations"]
    macro_series = {
        (row.get("instrument_id"), row.get("frequency")) for row in macro_rows
    }
    add("macro_observations 指标序列数量", 73, len(macro_series), len(macro_series) == 73)
    add(
        "macro_observations value 非空",
        0,
        sum(row.get("value") is None for row in macro_rows),
        all(row.get("value") is not None for row in macro_rows),
    )
    null_fields = ("previous_value", "forecast_value", "revised_value")
    add(
        "macro_observations 未虚构扩展值",
        0,
        sum(row.get(field) is not None for row in macro_rows for field in null_fields),
        all(row.get(field) is None for row in macro_rows for field in null_fields),
    )

    datasets = {
        row["dataset_id"]: row["storage_table_name"]
        for row in snapshot["dataset_catalog"]
        if row["dataset_id"] in {
            "LSEG_SPOT_PRICE",
            "LSEG_MARKET_BARS",
            "LSEG_MACRO",
            "LSEG_NEWS",
        }
    }
    expected_datasets = {
        "LSEG_SPOT_PRICE": "latest_prices",
        "LSEG_MARKET_BARS": "market_bars",
        "LSEG_MACRO": "macro_observations",
        "LSEG_NEWS": "news_articles",
    }
    add("四条路线数据集映射", expected_datasets, datasets, datasets == expected_datasets)

    field_rows_by_dataset: dict[str, set[str]] = {}
    for row in snapshot["dataset_field_catalog"]:
        field_rows_by_dataset.setdefault(row["dataset_id"], set()).add(row["field_name"])
    for dataset_id, required_fields in ROUTE_REQUIRED_FIELDS.items():
        actual_fields = field_rows_by_dataset.get(dataset_id, set())
        table_name = ROUTE_TABLES[dataset_id]
        physical_columns = set(snapshot[table_name][0]) if snapshot[table_name] else set()
        mapped_fields = {field.lower() for field in required_fields} & physical_columns
        add(
            f"字段目录覆盖 {dataset_id}",
            sorted(required_fields),
            sorted(actual_fields & required_fields),
            required_fields <= actual_fields,
        )
        add(
            f"字段映射到 source.{table_name}",
            sorted(field.lower() for field in required_fields),
            sorted(mapped_fields),
            mapped_fields == {field.lower() for field in required_fields},
        )

    article_ids = {row["article_id"] for row in snapshot["news_articles"]}
    news_cases = json.loads(NEWS_CASES.read_text(encoding="utf-8"))
    referenced_ids = {
        article_id
        for case in news_cases
        for article_id in (
            case["must_include_article_ids"] + case["must_not_include_article_ids"]
        )
    }
    missing_news_ids = sorted(referenced_ids - article_ids)
    add("新闻人工标注引用完整", [], missing_news_ids, not missing_news_ids)
    add("新闻人工标注用例数量", 7, len(news_cases), len(news_cases) == 7)
    return checks


def check_database_consistency(
    snapshot: dict[str, list[dict[str, Any]]],
    database: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """逐表比较 Excel 快照与当前 source 数据库的业务字段多重集合。"""

    checks: list[dict[str, Any]] = []
    for table, columns in DB_COLUMNS.items():
        expected = row_counter(snapshot[table], columns)
        actual = row_counter(database[table], columns)
        missing = list((expected - actual).elements())
        extra = list((actual - expected).elements())
        checks.append(
            {
                "name": f"数据库与快照一致 {table}",
                "expected": len(snapshot[table]),
                "actual": len(database[table]),
                "passed": not missing and not extra,
                "detail": f"missing={len(missing)}, extra={len(extra)}",
            }
        )
    return checks


def request_json(api_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """调用本地查询接口；报告只保存结果摘要，不保存密钥。"""

    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/search",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # HTTPError 默认只显示“500 Internal Server Error”，会掩盖模型 402、
        # 连接数据库失败等真正原因。读取本地接口返回体后写入当前用例详情，方便
        # 最终报告区分代码故障和外部服务额度/配置故障；响应体不会包含密钥。
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            body = ""
        detail = body[:1000] if body else exc.reason
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    """追加一条端到端断言，统一报告格式。"""

    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def evaluate_live_case(
    case: dict[str, Any],
    snapshot: dict[str, list[dict[str, Any]]],
    api_url: str,
) -> dict[str, Any]:
    """执行一个真实查询并检查路线、目录和最终业务结果。"""

    payload = {
        "query": case["query"],
        "route": case["route"],
        "limit": 3,
        "provider": None,
        "use_embedding": True,
        "use_candidate_llm": True,
        "row_limit": 100,
    }
    checks: list[dict[str, Any]] = []
    try:
        result = request_json(api_url, payload)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
        return {
            "case_id": case["case_id"],
            "query": case["query"],
            "route": case["route"],
            "passed": False,
            "checks": [{"name": "HTTP 查询", "passed": False, "detail": str(exc)}],
        }

    _check(checks, "路线枚举", result.get("route") == case["route"], str(result.get("route")))
    route_guard = result.get("route_guard") or {}
    _check(checks, "路线守卫", route_guard.get("accepted") is True, str(route_guard))

    if case["kind"] == "latest":
        price = result.get("price_result") or {}
        expected_row = next(
            row
            for row in snapshot["latest_prices"]
            if row["source_identifier"] == case["identifier"]
        )
        _check(checks, "数据集", price.get("dataset_id") == "LSEG_SPOT_PRICE", str(price.get("dataset_id")))
        _check(checks, "物理表", price.get("storage_table_name") == "latest_prices", str(price.get("storage_table_name")))
        _check(checks, "instrument_id", price.get("instrument_id") == case["instrument_id"], str(price.get("instrument_id")))
        _check(checks, "source_identifier", price.get("identifier") == case["identifier"], str(price.get("identifier")))
        rows = price.get("rows") or []
        _check(checks, "只返回最新一行", len(rows) == 1, f"row_count={len(rows)}")
        if rows:
            row = rows[0]
            for field in ("price_time", "last", "bid", "ask", "mid"):
                _check(
                    checks,
                    f"价格字段 {field}",
                    canonical_value(row.get(field), numeric=field in NUMERIC_COLUMNS)
                    == canonical_value(expected_row.get(field), numeric=field in NUMERIC_COLUMNS),
                    f"expected={expected_row.get(field)}, actual={row.get(field)}",
                )

    elif case["kind"] == "market_bars":
        bars = result.get("market_bars_result") or {}
        expected_rows = [
            row
            for row in snapshot["market_bars"]
            if row["source_identifier"] == case["identifier"]
            and case["start_date"] <= str(row["date"]) <= case["end_date"]
        ]
        _check(checks, "数据集", bars.get("dataset_id") == "LSEG_MARKET_BARS", str(bars.get("dataset_id")))
        _check(checks, "物理表", bars.get("storage_table_name") == "market_bars", str(bars.get("storage_table_name")))
        _check(checks, "instrument_id", bars.get("instrument_id") == case["instrument_id"], str(bars.get("instrument_id")))
        _check(checks, "source_identifier", bars.get("identifier") == case["identifier"], str(bars.get("identifier")))
        actual_rows = bars.get("rows") or []
        expected_keys = {str(row["date"]) for row in expected_rows}
        actual_keys = {str(row.get("date")) for row in actual_rows}
        _check(checks, "日线日期集合", actual_keys == expected_keys, f"expected={len(expected_keys)}, actual={len(actual_keys)}")
        _check(checks, "日线行数", len(actual_rows) == len(expected_rows), f"expected={len(expected_rows)}, actual={len(actual_rows)}")
        actual_dates = [str(row.get("date")) for row in actual_rows]
        _check(checks, "日线升序", actual_dates == sorted(actual_dates), str(actual_dates[:3]))
        expected_by_date = {str(row["date"]): row for row in expected_rows}
        for actual_row in actual_rows:
            expected_row = expected_by_date.get(str(actual_row.get("date")))
            if expected_row is None:
                continue
            for field in ("date", "open", "high", "low", "close", "volume"):
                _check(
                    checks,
                    f"K 线字段 {field} {actual_row.get('date')}",
                    canonical_value(actual_row.get(field), numeric=field in NUMERIC_COLUMNS)
                    == canonical_value(expected_row.get(field), numeric=field in NUMERIC_COLUMNS),
                    f"expected={expected_row.get(field)}, actual={actual_row.get(field)}",
                )

    elif case["kind"] == "market_bars_unsupported":
        request = result.get("market_bar_request") or {}
        bars = result.get("market_bars_result") or {}
        _check(checks, "月 K 线安全拒绝", request.get("status") == "unsupported", str(request))
        _check(checks, "未读取业务行", not (bars.get("rows") or []), str(bars.get("status")))

    elif case["kind"] == "macro":
        macro = result.get("macro_observations_result") or {}
        expected_rows = [
            row
            for row in snapshot["macro_observations"]
            if row["instrument_id"] == case["instrument_id"]
        ]
        expected_row = max(expected_rows, key=lambda row: str(row["release_time"]))
        _check(checks, "数据集", macro.get("dataset_id") == "LSEG_MACRO", str(macro.get("dataset_id")))
        _check(checks, "物理表", macro.get("storage_table_name") == "macro_observations", str(macro.get("storage_table_name")))
        _check(checks, "instrument_id", macro.get("instrument_id") == case["instrument_id"], str(macro.get("instrument_id")))
        rows = macro.get("rows") or []
        _check(checks, "默认返回最新一行", len(rows) == 1, f"row_count={len(rows)}")
        if rows:
            actual_data = rows[0].get("data") or {}
            _check(checks, "宏观数据字段边界", set(actual_data) == {"value", "previous_value", "forecast_value", "revised_value"}, str(actual_data.keys()))
            _check(checks, "宏观 value", canonical_value(actual_data.get("value"), numeric=True) == canonical_value(expected_row.get("value"), numeric=True), f"expected={expected_row.get('value')}, actual={actual_data.get('value')}")
            _check(checks, "宏观空值不虚构", all(actual_data.get(field) is None for field in ("previous_value", "forecast_value", "revised_value")), str(actual_data))

    elif case["kind"] == "macro_unsupported":
        field_resolution = result.get("field_resolution") or {}
        macro = result.get("macro_observations_result") or {}
        _check(checks, "利率字段安全停止", field_resolution.get("status") == "unsupported_dataset", str(field_resolution))
        _check(checks, "未读取宏观业务行", not (macro.get("rows") or []), str(macro.get("status")))

    passed = all(check["passed"] for check in checks)
    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "route": case["route"],
        "passed": passed,
        "checks": checks,
    }


def evaluate_news_case(
    case: dict[str, Any],
    api_url: str,
) -> dict[str, Any]:
    """执行人工标注新闻用例，检查必须召回和明确负样本。"""

    payload = {
        "query": case["query"],
        "route": "news_articles",
        "limit": 3,
        "provider": None,
        "use_embedding": True,
        "use_candidate_llm": True,
        "row_limit": 100,
    }
    checks: list[dict[str, Any]] = []
    try:
        result = request_json(api_url, payload)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
        return {
            "case_id": case["case_id"],
            "query": case["query"],
            "route": "news_articles",
            "label": case["label"],
            "passed": False,
            "checks": [{"name": "HTTP 查询", "passed": False, "detail": str(exc)}],
        }

    candidates = result.get("news_search", {}).get("candidates", [])
    candidate_ids = {candidate.get("article_id") for candidate in candidates}
    result_rows = result.get("news_result", {}).get("rows", [])
    result_ids = {
        row.get("metadata", {}).get("article_id") for row in result_rows
    }
    visible_ids = candidate_ids | result_ids
    for article_id in case["must_include_article_ids"]:
        _check(checks, f"必须召回 {article_id}", article_id in visible_ids, f"candidate_count={len(visible_ids)}")
    for article_id in case["must_not_include_article_ids"]:
        _check(checks, f"不得召回 {article_id}", article_id not in visible_ids, f"candidate_count={len(visible_ids)}")
    _check(checks, "新闻数据集", (result.get("dataset_resolution") or {}).get("dataset_id") == "LSEG_NEWS", str(result.get("dataset_resolution")))
    _check(checks, "新闻字段目录", (result.get("field_resolution") or {}).get("status") == "resolved", str(result.get("field_resolution")))
    _check(checks, "候选与源表行数一致", len(candidates) == len(result_rows), f"candidates={len(candidates)}, rows={len(result_rows)}")
    _check(checks, "候选不重复", len(candidate_ids) == len(candidates), f"candidate_count={len(candidates)}")
    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "route": "news_articles",
        "label": case["label"],
        "passed": all(check["passed"] for check in checks),
        "candidate_count": len(candidates),
        "checks": checks,
    }


def run_pytest() -> dict[str, Any]:
    """运行项目自动化测试，把摘要写入报告而不是吞掉失败。"""

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return {
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": output[-2000:],
    }


def markdown_value(value: Any) -> str:
    """把报告字段压缩成安全的 Markdown 文本。"""

    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_checks(title: str, checks: list[dict[str, Any]]) -> list[str]:
    """渲染统一的检查表。"""

    lines = [f"### {title}", "", "| 检查项 | 预期 | 实际 | 状态 | 说明 |", "|---|---|---|---|---|"]
    for check in checks:
        lines.append(
            "| {name} | {expected} | {actual} | {status} | {detail} |".format(
                name=markdown_value(check["name"]),
                expected=markdown_value(check.get("expected", "-")),
                actual=markdown_value(check.get("actual", "-")),
                status="PASS" if check["passed"] else "FAIL",
                detail=markdown_value(check.get("detail", "")),
            )
        )
    lines.append("")
    return lines


def render_live_cases(title: str, cases: list[dict[str, Any]]) -> list[str]:
    """渲染端到端用例和失败断言。"""

    lines = [f"### {title}", "", "| 用例 | 路线 | 标签 | 检查数 | 失败数 | 状态 |", "|---|---|---|---:|---:|---|"]
    for case in cases:
        failed = sum(not check["passed"] for check in case["checks"])
        lines.append(
            "| {case_id} | {route} | {label} | {total} | {failed} | {status} |".format(
                case_id=markdown_value(case["case_id"]),
                route=markdown_value(case["route"]),
                label=markdown_value(case.get("label", "-")),
                total=len(case["checks"]),
                failed=failed,
                status="PASS" if case["passed"] else "FAIL",
            )
        )
    lines.append("")
    for case in cases:
        failures = [check for check in case["checks"] if not check["passed"]]
        if failures:
            lines.append(f"#### {case['case_id']} 失败详情")
            lines.append("")
            for failure in failures:
                lines.append(f"- `{failure['name']}`：{failure['detail']}")
            lines.append("")
    return lines


def build_report(
    snapshot_path: Path,
    snapshot: dict[str, list[dict[str, Any]]],
    snapshot_checks: list[dict[str, Any]],
    db_checks: list[dict[str, Any]],
    pytest_result: dict[str, Any],
    live_cases: list[dict[str, Any]],
    news_cases: list[dict[str, Any]],
    live_enabled: bool,
) -> str:
    """组合最终 Markdown 报告。"""

    all_checks = snapshot_checks + db_checks
    passed_checks = sum(check["passed"] for check in all_checks)
    total_checks = len(all_checks)
    all_live_cases = live_cases + news_cases
    live_summary = (
        f"{sum(item['passed'] for item in all_live_cases)}/{len(all_live_cases)} 通过"
        if live_enabled
        else "未执行"
    )
    lines = [
        "# ICBC Trading AI Search 四条链路准确性测试报告",
        "",
        f"> 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"> 测试快照：`{snapshot_path}`",
        "> 本报告只读数据库，不修改 `source` 业务表。",
        "",
        "## 一、总体结论",
        "",
        f"- 快照和数据库一致性检查：{passed_checks}/{total_checks} 通过。",
        f"- 项目自动化测试：{'通过' if pytest_result['passed'] else '失败'}。",
        f"- 真实端到端测试：{live_summary}，共 {len(all_live_cases)} 个用例。",
        "- 新闻相关性使用人工标注样本；由于 `source.news_articles` 没有金融工具外键，新闻结果不能用普通行情链路的精确外键规则判定。",
        "",
        "## 二、快照数据覆盖",
        "",
        (
            "| 业务表 | 实际行数 | 测试基准 |\n"
            "|---|---:|---|\n"
            "| `source.latest_prices` | {latest} | 15 个供应商标识、最新报价字段 |\n"
            "| `source.market_bars` | {bars} | 6 个日线序列、127 条 K 线 |\n"
            "| `source.macro_observations` | {macro} | 73 个指标序列、3 种频率 |\n"
            "| `source.news_articles` | {news} | 76 篇文章、7 个人工标注查询 |\n"
        ).format(
            latest=len(snapshot["latest_prices"]),
            bars=len(snapshot["market_bars"]),
            macro=len(snapshot["macro_observations"]),
            news=len(snapshot["news_articles"]),
        ),
        "## 三、快照和目录检查",
        "",
    ]
    if live_enabled:
        failure_text = " ".join(
            str(check.get("detail", ""))
            for case in all_live_cases
            for check in case.get("checks", [])
            if not check.get("passed")
        )
        if "402" in failure_text and "Payment Required" in failure_text:
            # 402 是外部聊天模型额度/计费状态，不代表四张 source 表或目录关系错误。
            # 将它单独列在总体结论中，避免阅读者把所有用例失败误判为 SQL 或字段问题。
            blocker_note = (
                "- 在线测试阻断原因：查询解析聊天模型返回 `HTTP 402 Payment Required`；本次不能据此评价模型参与的完整在线链路，离线数据和目录校验仍然有效。"
            )
            section_index = lines.index("## 二、快照数据覆盖")
            lines[section_index:section_index] = [blocker_note, ""]
    lines.extend(render_checks("数据与目录基准", snapshot_checks))
    lines.extend(render_checks("数据库与 Excel 快照一致性", db_checks))
    lines.extend(
        [
            "## 四、自动化测试",
            "",
            "```text",
            pytest_result["output"],
            "```",
            "",
            "## 五、真实端到端测试",
            "",
        ]
    )
    if live_enabled:
        lines.extend(render_live_cases("行情和宏观路线", live_cases))
        lines.extend(render_live_cases("新闻人工标注路线", news_cases))
    else:
        lines.extend(["本次未启用 `--live`，没有调用真实模型和本地 HTTP 查询接口。", ""])
    lines.extend(
        [
            "## 六、判定口径和剩余风险",
            "",
            "1. `latest_prices`、`market_bars` 和 `macro_observations` 使用快照业务行作为独立标准答案，重点判断工具、供应商标识、数据集、字段和最终值是否全部一致。",
            "2. `news_articles` 使用 `must_include_article_ids` 和 `must_not_include_article_ids` 进行人工标注校验；`likely_relevant` 代表语义相关，不等于存在数据库外键关系。",
            "3. 新闻候选不限制最终条数，但 Embedding 仍使用 `NEWS_EMBEDDING_MIN_SCORE` 过滤低相关结果；这属于相关性门槛，不是返回数量上限。",
            "4. 当前宏观路线对 `LSEG_MACRO` 的字段有完整标准答案；`LSEG_INTEREST_RATE` 和 `LSEG_BOND_YIELD` 当前应验证为安全停止，不能当作查询成功。",
            "5. 快照只有日线 `market_bars`，月、季、年和小时 K 线只能验证为不支持，不能据此评价未来聚合能力。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    """解析报告生成参数。"""

    parser = argparse.ArgumentParser(description="生成四条 AI Search 准确性测试报告")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-url", default="http://127.0.0.1:8011")
    parser.add_argument("--live", action="store_true", help="执行真实本地 HTTP 和模型查询")
    parser.add_argument("--skip-db", action="store_true", help="跳过 source 数据库与 Excel 快照比较")
    parser.add_argument("--skip-pytest", action="store_true", help="跳过项目 pytest")
    return parser.parse_args()


def main() -> int:
    """生成报告并返回适合命令行使用的状态码。"""

    args = parse_args()
    snapshot = read_snapshot(args.snapshot)
    snapshot_checks = check_snapshot(snapshot)
    db_checks: list[dict[str, Any]] = []
    if not args.skip_db:
        try:
            database = read_database_snapshot()
            db_checks = check_database_consistency(snapshot, database)
        except Exception as exc:  # noqa: BLE001 - 报告要记录环境问题而不是吞掉
            db_checks = [
                {
                    "name": "读取 source 数据库",
                    "expected": "可只读连接",
                    "actual": type(exc).__name__,
                    "passed": False,
                    "detail": str(exc),
                }
            ]

    pytest_result = {
        "passed": True,
        "returncode": 0,
        "output": "已跳过 pytest",
    }
    if not args.skip_pytest:
        pytest_result = run_pytest()

    live_cases: list[dict[str, Any]] = []
    news_cases: list[dict[str, Any]] = []
    if args.live:
        for case in LIVE_CASES:
            live_cases.append(evaluate_live_case(case, snapshot, args.api_url))
        for case in json.loads(NEWS_CASES.read_text(encoding="utf-8")):
            news_cases.append(evaluate_news_case(case, args.api_url))

    report = build_report(
        args.snapshot,
        snapshot,
        snapshot_checks,
        db_checks,
        pytest_result,
        live_cases,
        news_cases,
        args.live,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"accuracy report written: {args.output}")
    print(f"snapshot checks: {sum(item['passed'] for item in snapshot_checks)}/{len(snapshot_checks)}")
    print(f"database checks: {sum(item['passed'] for item in db_checks)}/{len(db_checks)}")
    print(f"pytest: {'passed' if pytest_result['passed'] else 'failed'}")
    if args.live:
        all_live = live_cases + news_cases
        print(f"live cases: {sum(item['passed'] for item in all_live)}/{len(all_live)}")
    # 在线测试属于最终准确性报告的必要组成部分；启用 ``--live`` 时，任何
    # HTTP、模型、目录、字段或业务值断言失败都必须让命令返回非零状态，避免
    # CI 或人工执行时只看到“报告已生成”却误以为全链路通过。
    live_passed = True
    if args.live:
        live_passed = all(item["passed"] for item in live_cases + news_cases)
    return 0 if pytest_result["passed"] and all(item["passed"] for item in snapshot_checks + db_checks) and live_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
