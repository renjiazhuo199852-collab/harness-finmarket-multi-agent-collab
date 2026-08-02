"""结构化 AI 查询计划的校验和安全执行。

本阶段只开放 ``LSEG_SPOT_PRICE -> source.latest_prices`` 这一条首验收路径，
但路径中的工具、供应商代码、数据集和字段都从 AI 数据库目录解析。代码不会
写死 EURUSD，也不会执行 Agent 传入的原始 SQL；SQL 中的表名和列名来自经过
白名单检查的目录记录，过滤值始终使用 psycopg 参数绑定。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from src.ai_query.catalog_search import CatalogSearchError, quote_identifier
from src.config.accessor import get_env_config
from src.config.env_schema import AIQueryConfig
from src.market_database import MarketDatabaseClient, MarketDatabaseUnavailable

_ALLOWED_OPERATORS = frozenset({"eq", "in", "gt", "gte", "lt", "lte"})
_SQL_OPERATORS = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}
_ALLOWED_DIRECTIONS = frozenset({"asc", "desc"})
_SUPPORTED_FIRST_DATASET = "LSEG_SPOT_PRICE"
_INSTRUMENT_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9./=_-]{1,50}")


class AIQueryPlanError(ValueError):
    """表示查询计划不合法、越权或当前阶段尚未支持。"""


class AIQueryExecutor:
    """根据目录白名单执行只读结构化查询计划。"""

    def __init__(
        self,
        client: MarketDatabaseClient | Any | None = None,
        *,
        config: AIQueryConfig | None = None,
    ) -> None:
        self._config = config or get_env_config().ai_query
        self._client = client or self._build_client()

    @property
    def is_configured(self) -> bool:
        """返回 AI 数据库是否已显式配置。"""
        return bool(self._client.is_configured)

    def execute(self, plan: Any) -> dict[str, Any]:
        """校验计划并执行第一阶段的最新报价查询。"""
        parsed = _validate_plan(plan, max_rows=self._config.max_rows)
        if not self.is_configured:
            raise MarketDatabaseUnavailable(
                "AI query database is not configured; set AI_QUERY_ENABLED and AI_QUERY_DB_* values"
            )
        if parsed["dataset_id"] != _SUPPORTED_FIRST_DATASET:
            raise AIQueryPlanError(
                f"第一阶段暂只支持数据集 {_SUPPORTED_FIRST_DATASET}，收到 {parsed['dataset_id']}。"
            )

        policy = self._load_dataset_policy(parsed["dataset_id"])
        mappings = self._load_field_mappings(parsed["dataset_id"], parsed)
        instrument = self._resolve_instrument(parsed["entity"]["value"])
        query, params = self._build_latest_prices_query(
            parsed, policy, mappings, instrument
        )
        rows = self._client.fetch_all(query, params)
        # 先用数据库返回的 datetime 判断时效，再把结果转换成 JSON 字符串；
        # 如果先序列化，时间类型会丢失，无法可靠计算报价年龄。
        warnings = _freshness_warnings(rows, self._config.stale_after_seconds)
        data = [_json_safe(row) for row in rows]
        if not data:
            warnings.append("没有找到符合当前查询计划的最新报价。")

        return {
            "ok": True,
            "dataset_id": parsed["dataset_id"],
            "storage_table_name": policy["storage_table_name"],
            "source_version": policy["source_version"],
            "instrument": _json_safe(instrument),
            "fields": parsed["select"],
            "count": len(data),
            "data": data,
            "warnings": warnings,
        }

    def _build_client(self) -> MarketDatabaseClient:
        """把 AIQueryConfig 适配到项目已有的只读 PostgreSQL 客户端。"""
        from src.config.env_schema import MarketDatabaseConfig

        return MarketDatabaseClient(
            MarketDatabaseConfig(
                enabled=self._config.enabled,
                host=self._config.host,
                port=self._config.port,
                database=self._config.database,
                user=self._config.user,
                password=self._config.password,
                connect_timeout_seconds=self._config.connect_timeout_seconds,
                statement_timeout_ms=self._config.statement_timeout_ms,
            )
        )

    def _load_dataset_policy(self, dataset_id: str) -> dict[str, Any]:
        """从数据集白名单读取实际存储表和快照版本。"""
        rows = self._client.fetch_all(
            """
            SELECT dataset_id, storage_table_name, is_queryable, source_version
            FROM ai.dataset_policy
            WHERE policy_key = %s
            """,
            (f"dataset:{dataset_id}",),
        )
        if not rows or not rows[0].get("is_queryable"):
            raise AIQueryPlanError(f"数据集 {dataset_id} 未登记或未获准查询。")
        policy = rows[0]
        if policy.get("storage_table_name") != "latest_prices":
            raise AIQueryPlanError("第一阶段的执行器只允许查询 latest_prices。")
        return policy

    def _load_field_mappings(
        self, dataset_id: str, plan: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """校验计划字段并读取业务字段到实际列的映射。"""
        requested: list[str] = list(plan["select"])
        requested.extend(item["field"] for item in plan["filters"])
        requested.extend(item["field"] for item in plan["order_by"])
        field_names = list(dict.fromkeys(name.upper() for name in requested))
        rows = self._client.fetch_all(
            """
            SELECT source_field_name, storage_schema, storage_table,
                   storage_column, is_filterable, is_selectable
            FROM ai.field_mapping
            WHERE dataset_id = %s
              AND upper(source_field_name) = ANY(%s)
            """,
            (dataset_id, field_names),
        )
        mappings = {str(row["source_field_name"]).upper(): row for row in rows}
        missing = [name for name in field_names if name not in mappings]
        if missing:
            raise AIQueryPlanError(f"以下字段未在字段目录中登记：{', '.join(missing)}")
        for name in plan["select"]:
            if not mappings[name.upper()].get("is_selectable"):
                raise AIQueryPlanError(f"字段 {name} 不允许返回。")
        for item in plan["filters"]:
            if not mappings[item["field"].upper()].get("is_filterable"):
                raise AIQueryPlanError(f"字段 {item['field']} 不允许筛选。")
        for name, mapping in mappings.items():
            if (
                mapping.get("storage_schema") != "source"
                or mapping.get("storage_table") != "latest_prices"
            ):
                raise AIQueryPlanError(f"字段 {name} 的存储位置不符合第一阶段白名单。")
            try:
                quote_identifier(str(mapping["storage_column"]))
            except CatalogSearchError as exc:
                raise AIQueryPlanError(str(exc)) from exc
        return mappings

    def _resolve_instrument(self, value: Any) -> dict[str, Any]:
        """把 EURUSD、EUR/USD、FX_EURUSD 或 EUR= 解析为唯一工具。"""
        if not isinstance(value, str) or not value.strip():
            raise AIQueryPlanError("entity.value 必须是非空工具代码。")
        raw_aliases = list(
            dict.fromkeys(_INSTRUMENT_IDENTIFIER_RE.findall(value.upper()))
        )
        raw_aliases.append(value.strip().upper())
        raw_aliases = list(dict.fromkeys(raw_aliases))
        normalized = sorted(
            {_normalize_code(alias) for alias in raw_aliases if _normalize_code(alias)}
        )
        rows = self._client.fetch_all(
            """
            SELECT im.instrument_id, im.canonical_symbol, im.name,
                   im.description, ii.provider, ii.identifier_type, ii.identifier
            FROM source.instrument_master AS im
            LEFT JOIN source.instrument_identifier AS ii
              ON ii.instrument_id = im.instrument_id
            WHERE upper(im.instrument_id) = ANY(%s)
               OR upper(im.canonical_symbol) = ANY(%s)
               OR regexp_replace(upper(im.canonical_symbol), '[^A-Z0-9]', '', 'g') = ANY(%s)
               OR upper(ii.identifier) = ANY(%s)
            ORDER BY im.instrument_id, ii.provider, ii.identifier
            """,
            (
                raw_aliases,
                raw_aliases,
                normalized or ["__NO_ALIAS__"],
                raw_aliases,
            ),
        )
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            instrument_id = str(row["instrument_id"])
            item = by_id.setdefault(
                instrument_id,
                {
                    "instrument_id": instrument_id,
                    "canonical_symbol": row.get("canonical_symbol"),
                    "name": row.get("name"),
                    "description": row.get("description"),
                    "identifiers": [],
                },
            )
            if row.get("provider") and row.get("identifier"):
                item["identifiers"].append(
                    {
                        "provider": row.get("provider"),
                        "identifier_type": row.get("identifier_type"),
                        "identifier": row.get("identifier"),
                    }
                )
        if not by_id:
            raise AIQueryPlanError(f"未找到工具 {value!r}。")
        if len(by_id) > 1:
            raise AIQueryPlanError(f"工具 {value!r} 对应多个金融工具，无法安全执行。")
        return next(iter(by_id.values()))

    def _build_latest_prices_query(
        self,
        plan: dict[str, Any],
        policy: dict[str, Any],
        mappings: dict[str, dict[str, Any]],
        instrument: dict[str, Any],
    ) -> tuple[str, tuple[Any, ...]]:
        """根据已校验目录生成报价 SQL；所有用户值都放入 params。"""
        try:
            schema = quote_identifier("source")
            table = quote_identifier(str(policy["storage_table_name"]))
            select_parts = [
                f"lp.{quote_identifier(str(mappings[name.upper()]['storage_column']))}"
                for name in plan["select"]
            ]
            where_parts = ["ii.instrument_id = %s"]
        except (KeyError, CatalogSearchError) as exc:
            raise AIQueryPlanError("查询计划字段无法映射到安全数据库列。") from exc

        params: list[Any] = [instrument["instrument_id"]]
        for item in plan["filters"]:
            field = mappings[item["field"].upper()]
            column = f"lp.{quote_identifier(str(field['storage_column']))}"
            operator = item["operator"]
            value = item["value"]
            if operator == "eq":
                where_parts.append(f"{column} = %s")
                params.append(value)
            elif operator == "in":
                placeholders = ", ".join("%s" for _ in value)
                where_parts.append(f"{column} IN ({placeholders})")
                params.extend(value)
            else:
                where_parts.append(f"{column} {_SQL_OPERATORS[operator]} %s")
                params.append(value)

        order_parts = []
        for item in plan["order_by"]:
            field = mappings[item["field"].upper()]
            order_parts.append(
                f"lp.{quote_identifier(str(field['storage_column']))} {item['direction'].upper()}"
            )
        if not order_parts:
            price_time = mappings.get("PRICE_TIME")
            if price_time is None:
                raise AIQueryPlanError("latest_prices 查询必须登记 PRICE_TIME 字段。")
            order_parts.append(
                f"lp.{quote_identifier(str(price_time['storage_column']))} DESC"
            )

        params.append(plan["limit"])
        query = f"""
            SELECT {', '.join(select_parts)}
            FROM {schema}.{table} AS lp
            JOIN "source"."instrument_identifier" AS ii
              ON ii.provider = lp.source
             AND ii.identifier = lp.source_identifier
            WHERE {' AND '.join(where_parts)}
            ORDER BY {', '.join(order_parts)}
            LIMIT %s
        """
        return query, tuple(params)


def _validate_plan(value: Any, *, max_rows: int) -> dict[str, Any]:
    """校验 Agent 计划的结构，拒绝原始 SQL 和未定义字段。"""
    if not isinstance(value, dict):
        raise AIQueryPlanError("查询计划必须是 JSON 对象。")
    allowed_keys = {"dataset_id", "entity", "select", "filters", "order_by", "limit"}
    unknown_keys = sorted(set(value) - allowed_keys)
    if unknown_keys:
        raise AIQueryPlanError(f"查询计划包含不允许的字段：{', '.join(unknown_keys)}")

    dataset_id = value.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise AIQueryPlanError("dataset_id 必须是非空字符串。")
    entity = value.get("entity")
    if not isinstance(entity, dict) or entity.get("type") != "instrument":
        raise AIQueryPlanError("entity 必须是 type=instrument 的对象。")
    if not isinstance(entity.get("value"), str) or not entity["value"].strip():
        raise AIQueryPlanError("entity.value 必须是非空字符串。")

    selected = value.get("select")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) for item in selected)
    ):
        raise AIQueryPlanError("select 必须是非空字符串数组。")
    selected_names = [item.strip().upper() for item in selected]
    if len(set(selected_names)) != len(selected_names):
        raise AIQueryPlanError("select 不能包含重复字段。")

    filters = _validate_filters(value.get("filters", []))
    order_by = _validate_order_by(value.get("order_by", []))
    limit = value.get("limit", 1)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= max_rows
    ):
        raise AIQueryPlanError(f"limit 必须是 1 到 {max_rows} 之间的整数。")
    return {
        "dataset_id": dataset_id.strip().upper(),
        "entity": {"type": "instrument", "value": entity["value"].strip()},
        "select": selected_names,
        "filters": filters,
        "order_by": order_by,
        "limit": limit,
    }


def _validate_filters(value: Any) -> list[dict[str, Any]]:
    """校验字段过滤器，只允许有限运算符和标量/数组参数。"""
    if not isinstance(value, list):
        raise AIQueryPlanError("filters 必须是数组。")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"field", "operator", "value"}:
            raise AIQueryPlanError("每个 filter 必须包含 field、operator、value。")
        field = item["field"]
        operator = item["operator"]
        if not isinstance(field, str) or not field.strip():
            raise AIQueryPlanError("filter.field 必须是非空字符串。")
        if operator not in _ALLOWED_OPERATORS:
            raise AIQueryPlanError(f"不允许的过滤运算符：{operator}")
        current_value = item["value"]
        if operator == "in":
            if (
                not isinstance(current_value, list)
                or not current_value
                or len(current_value) > 20
            ):
                raise AIQueryPlanError("IN 过滤值必须是 1 到 20 项的数组。")
        elif isinstance(current_value, (dict, list)):
            raise AIQueryPlanError("非 IN 过滤器只能使用标量值。")
        result.append(
            {
                "field": field.strip().upper(),
                "operator": operator,
                "value": current_value,
            }
        )
    return result


def _validate_order_by(value: Any) -> list[dict[str, str]]:
    """校验排序字段和方向。"""
    if not isinstance(value, list) or len(value) > 5:
        raise AIQueryPlanError("order_by 必须是最多 5 项的数组。")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"field", "direction"}:
            raise AIQueryPlanError("每个 order_by 必须包含 field、direction。")
        if (
            not isinstance(item["field"], str)
            or item["direction"] not in _ALLOWED_DIRECTIONS
        ):
            raise AIQueryPlanError("order_by 的字段或方向不合法。")
        result.append(
            {"field": item["field"].strip().upper(), "direction": item["direction"]}
        )
    return result


def _normalize_code(value: str) -> str:
    """统一 EURUSD、EUR/USD 等工具代码的可比较形式。"""
    return "".join(character for character in value.upper() if character.isalnum())


def _freshness_warnings(
    rows: list[dict[str, Any]], stale_after_seconds: int
) -> list[str]:
    """根据最新报价时间生成数据时效提示，不把旧快照伪装成实时数据。"""
    if not rows:
        return []
    timestamps = [
        row.get("price_time") for row in rows if row.get("price_time") is not None
    ]
    if not timestamps:
        return ["结果没有 price_time，无法判断行情时效。"]
    latest = max(_as_utc(value) for value in timestamps)
    age_seconds = (datetime.now(timezone.utc) - latest).total_seconds()
    if age_seconds > stale_after_seconds:
        return [f"报价数据距当前约 {int(age_seconds)} 秒，超过配置的时效阈值。"]
    return []


def _as_utc(value: Any) -> datetime:
    """把数据库时间统一转换为带时区的 UTC 时间。"""
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc)
            if value.tzinfo
            else value.replace(tzinfo=timezone.utc)
        )
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise AIQueryPlanError("price_time 不是可识别的时间类型。")


def _json_safe(value: Any) -> Any:
    """递归转换 PostgreSQL 数值和时间，使查询结果可直接序列化为 JSON。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
