"""Phase 2 PostgreSQL 市场数据的共享只读查询入口。

这个模块只处理项目内部市场数据库的查询语义：先把用户输入的标准代码
（例如 ``EURUSD``）解析为 ``instrument_id``，再按四张业务表各自正确的
关联路径读取数据。它不定义 Agent Tool，也不处理 MCP 协议，因此四个 Tool
可以共用相同的参数校验、SQL 和结果形状，而不会复制数据库访问逻辑。

所有 SQL 文本都固定在本模块中，来自 Agent 的输入只能作为 psycopg 参数
绑定。这样既避免字符串拼接 SQL，也让数据库连接层始终保持只读事务。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.market_database import MarketDatabaseClient

# 这些取值与 Phase 2 的 public.frequency_enum 一致。先在 Tool 层报出清晰
# 错误，避免把拼写错误静默变成一条空查询结果。
_FREQUENCIES = frozenset(
    {
        "realtime",
        "tick",
        "minute",
        "hourly",
        "daily",
        "weekly",
        "monthly",
        "quarterly",
        "yearly",
    }
)

_DEFAULT_BARS_LIMIT = 250
_MAX_BARS_LIMIT = 1000
_DEFAULT_MACRO_LIMIT = 100
_MAX_MACRO_LIMIT = 500
_DEFAULT_NEWS_LIMIT = 50
_MAX_NEWS_LIMIT = 200


class MarketDataReaderError(ValueError):
    """表示 Tool 参数或内部主数据解析失败的可预期错误。"""


class MarketDataReader:
    """按 Phase 2 关系模型读取内部市场数据。

    Args:
        client: 可选的数据库客户端注入点。生产环境省略时自动使用共享的
            :class:`MarketDatabaseClient`；单元测试可传入假的只读客户端，
            因而不需要网络、SSH 隧道或真实数据库密码。
    """

    def __init__(self, client: MarketDatabaseClient | Any | None = None) -> None:
        self._client = client or MarketDatabaseClient()

    @property
    def is_configured(self) -> bool:
        """返回当前环境是否已显式配置并启用市场数据库。"""
        return bool(self._client.is_configured)

    def get_market_bars(
        self,
        symbol: Any,
        *,
        source: Any = None,
        frequency: Any = None,
        start_date: Any = None,
        end_date: Any = None,
        limit: Any = _DEFAULT_BARS_LIMIT,
    ) -> dict[str, Any]:
        """读取一个工具的历史 OHLCV K 线。

        ``market_bars`` 已直接保存 ``instrument_id``，因此查询不依赖
        ``source_identifier`` 进行业务关联；后者仅随结果返回，用于追溯
        LSEG 等供应商的原始代码。
        """
        instrument = self._resolve_instrument(symbol)
        clean_source = _optional_code(source, "source", 20)
        clean_frequency = _optional_frequency(frequency)
        start, end = _date_window(start_date, end_date)
        row_limit = _bounded_limit(limit, _DEFAULT_BARS_LIMIT, _MAX_BARS_LIMIT)

        rows = self._client.fetch_all(
            """
            SELECT
                bar_date, bar_time, frequency::text AS frequency,
                open, high, low, close, volume,
                source::text AS source,
                source_identifier_type, source_identifier
            FROM public.market_bars
            WHERE instrument_id = %s
              AND (%s::text IS NULL OR source::text = %s::text)
              AND (%s::text IS NULL OR frequency::text = %s::text)
              AND (%s::date IS NULL OR bar_date >= %s::date)
              AND (%s::date IS NULL OR bar_date <= %s::date)
            ORDER BY bar_time DESC
            LIMIT %s
            """,
            (
                instrument["instrument_id"],
                clean_source,
                clean_source,
                clean_frequency,
                clean_frequency,
                start,
                start,
                end,
                end,
                row_limit,
            ),
        )
        return {"instrument": instrument, "bars": rows, "count": len(rows)}

    def get_latest_prices(
        self,
        symbol: Any,
        *,
        source: Any = None,
    ) -> dict[str, Any]:
        """读取一个工具在一个或多个供应商下的当前报价快照。

        Phase 2 对 ``(instrument_id, source)`` 有唯一约束，所以结果中每个
        数据源至多一行，而不是需要由应用程序猜测哪一行才是最新报价。
        """
        instrument = self._resolve_instrument(symbol)
        clean_source = _optional_code(source, "source", 20)
        rows = self._client.fetch_all(
            """
            SELECT
                price_time, last_price, bid, ask, mid_price,
                source::text AS source,
                source_identifier_type, source_identifier
            FROM public.latest_prices
            WHERE instrument_id = %s
              AND (%s::text IS NULL OR source::text = %s::text)
            ORDER BY source ASC
            """,
            (instrument["instrument_id"], clean_source, clean_source),
        )
        return {"instrument": instrument, "prices": rows, "count": len(rows)}

    def get_macro_observations(
        self,
        symbol: Any,
        *,
        metric_ids: Any = None,
        source: Any = None,
        start_date: Any = None,
        end_date: Any = None,
        limit: Any = _DEFAULT_MACRO_LIMIT,
    ) -> dict[str, Any]:
        """读取与一个工具存在正式关联的宏观指标发布记录。

        关联规则来自 ``instrument_metric_link``，而真实发布数值仍只存于
        ``macro_observations``。这样一条美国 CPI 不会因同时影响多个工具而
        被复制多份，也不需要在宏观观测表错误地加入单一 ``instrument_id``。
        """
        instrument = self._resolve_instrument(symbol)
        clean_metric_ids = _metric_id_list(metric_ids)
        clean_source = _optional_code(source, "source", 20)
        start, end = _date_window(start_date, end_date)
        row_limit = _bounded_limit(limit, _DEFAULT_MACRO_LIMIT, _MAX_MACRO_LIMIT)

        rows = self._client.fetch_all(
            """
            SELECT
                im.relationship_role::text AS relationship_role,
                mc.metric_id, mc.metric_name, mc.description AS metric_description,
                mc.category AS metric_category, mc.frequency::text AS metric_frequency,
                mo.release_time, mo.frequency::text AS frequency,
                mo.value, mo.previous_value, mo.forecast_value, mo.revised_value,
                mo.source::text AS source, mo.source_identifier,
                mo.country, mo.region, mo.unit
            FROM public.instrument_metric_link AS im
            JOIN public.metric_catalog AS mc
              ON mc.metric_id = im.metric_id
            JOIN public.macro_observations AS mo
              ON mo.metric_id = im.metric_id
            WHERE im.instrument_id = %s
              AND (%s::text[] IS NULL OR mo.metric_id = ANY(%s::text[]))
              AND (%s::text IS NULL OR mo.source::text = %s::text)
              AND (%s::date IS NULL OR mo.release_time >= %s::date)
              AND (%s::date IS NULL OR mo.release_time < (%s::date + INTERVAL '1 day'))
            ORDER BY mo.release_time DESC, mo.metric_id ASC
            LIMIT %s
            """,
            (
                instrument["instrument_id"],
                clean_metric_ids,
                clean_metric_ids,
                clean_source,
                clean_source,
                start,
                start,
                end,
                end,
                row_limit,
            ),
        )
        return {
            "instrument": instrument,
            "observations": rows,
            "count": len(rows),
        }

    def get_news(
        self,
        symbol: Any,
        *,
        source: Any = None,
        start_date: Any = None,
        end_date: Any = None,
        limit: Any = _DEFAULT_NEWS_LIMIT,
    ) -> dict[str, Any]:
        """读取与一个工具正式关联的新闻文章。

        新闻与工具是多对多关系，因此通过 ``news_instrument_link`` 进行
        内连接。``keywords`` 仍只是文章主题 JSONB，不作为工具关联依据。
        """
        instrument = self._resolve_instrument(symbol)
        clean_source = _optional_code(source, "source", 20)
        start, end = _date_window(start_date, end_date)
        row_limit = _bounded_limit(limit, _DEFAULT_NEWS_LIMIT, _MAX_NEWS_LIMIT)

        rows = self._client.fetch_all(
            """
            SELECT
                n.id, n.article_id, n.source::text AS source, n.publish_time,
                n.title, n.content, n.summary, n.url, n.language,
                n.sentiment_score, n.relevance_score, n.keywords
            FROM public.news_articles AS n
            JOIN public.news_instrument_link AS link
              ON link.news_id = n.id
            WHERE link.instrument_id = %s
              AND (%s::text IS NULL OR n.source::text = %s::text)
              AND (%s::date IS NULL OR n.publish_time >= %s::date)
              AND (%s::date IS NULL OR n.publish_time < (%s::date + INTERVAL '1 day'))
            ORDER BY n.publish_time DESC, n.id DESC
            LIMIT %s
            """,
            (
                instrument["instrument_id"],
                clean_source,
                clean_source,
                start,
                start,
                end,
                end,
                row_limit,
            ),
        )
        return {"instrument": instrument, "articles": rows, "count": len(rows)}

    def _resolve_instrument(self, symbol: Any) -> dict[str, Any]:
        """把标准代码解析为 Phase 2 内部工具主数据记录。"""
        canonical_symbol = _required_symbol(symbol)
        rows = self._client.fetch_all(
            """
            SELECT instrument_id, canonical_symbol, name, instrument_type::text AS instrument_type,
                   country, region, currency, status::text AS status
            FROM public.instrument_master
            WHERE canonical_symbol = %s
            """,
            (canonical_symbol,),
        )
        if not rows:
            raise MarketDataReaderError(
                f"未找到标准工具代码 '{canonical_symbol}'，请先查询 instrument_master。"
            )
        return rows[0]


def _required_symbol(value: Any) -> str:
    """校验并标准化必填的内部标准代码。"""
    if not isinstance(value, str) or not value.strip():
        raise MarketDataReaderError("symbol 必填，且必须是非空字符串，例如 'EURUSD'。")
    symbol = value.strip().upper()
    if len(symbol) > 50:
        raise MarketDataReaderError("symbol 长度不能超过 50 个字符。")
    return symbol


def _optional_code(value: Any, field_name: str, max_length: int) -> str | None:
    """标准化可选的受控文本筛选项，例如供应商名称。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise MarketDataReaderError(f"{field_name} 必须是非空字符串。")
    clean_value = value.strip().upper()
    if len(clean_value) > max_length:
        raise MarketDataReaderError(f"{field_name} 长度不能超过 {max_length} 个字符。")
    return clean_value


def _optional_frequency(value: Any) -> str | None:
    """校验可选频率，使其与数据库枚举的允许值保持一致。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MarketDataReaderError("frequency 必须是字符串。")
    frequency = value.strip().lower()
    if frequency not in _FREQUENCIES:
        choices = ", ".join(sorted(_FREQUENCIES))
        raise MarketDataReaderError(f"frequency 必须是以下之一：{choices}。")
    return frequency


def _metric_id_list(value: Any) -> list[str] | None:
    """校验可选指标列表，并保留调用方给出的稳定顺序。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise MarketDataReaderError("metric_ids 必须是字符串数组。")

    metric_ids: list[str] = []
    seen: set[str] = set()
    for raw_metric_id in value:
        if not isinstance(raw_metric_id, str) or not raw_metric_id.strip():
            raise MarketDataReaderError("metric_ids 中每一项都必须是非空字符串。")
        metric_id = raw_metric_id.strip().upper()
        if len(metric_id) > 50:
            raise MarketDataReaderError("metric_ids 中的指标代码不能超过 50 个字符。")
        if metric_id not in seen:
            metric_ids.append(metric_id)
            seen.add(metric_id)
    return metric_ids or None


def _date_window(start_value: Any, end_value: Any) -> tuple[str | None, str | None]:
    """校验可选的闭区间日期筛选，日期格式固定为 ``YYYY-MM-DD``。"""
    start = _optional_date(start_value, "start_date")
    end = _optional_date(end_value, "end_date")
    if start and end and start > end:
        raise MarketDataReaderError("start_date 不能晚于 end_date。")
    return start, end


def _optional_date(value: Any, field_name: str) -> str | None:
    """验证一个可选 ISO 日期，并返回数据库可直接绑定的标准字符串。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MarketDataReaderError(f"{field_name} 必须使用 YYYY-MM-DD 格式。")
    clean_value = value.strip()
    try:
        return date.fromisoformat(clean_value).isoformat()
    except ValueError as exc:
        raise MarketDataReaderError(f"{field_name} 必须使用 YYYY-MM-DD 格式。") from exc


def _bounded_limit(value: Any, default: int, maximum: int) -> int:
    """把 Tool 返回行数限制在有限范围内，防止 Agent 获得过大载荷。"""
    if value is None:
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataReaderError("limit 必须是整数。") from exc
    if not 1 <= limit <= maximum:
        raise MarketDataReaderError(f"limit 必须在 1 到 {maximum} 之间。")
    return limit
