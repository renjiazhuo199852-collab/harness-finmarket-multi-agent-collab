"""Generate and optionally seed a complete synthetic FX evidence bundle.

The Excel output keeps the legacy four-sheet contract. The optional database
writer targets only the existing ``source`` tables and uses the provider and
instrument identifiers already registered by the MCP catalog. It is intended
for a dedicated test database: rows are synthetic even though the provider
column is ``LSEG`` so the current catalog can resolve them.

The database flag is deliberately opt-in and requires ``--confirm-test-data``.
The script never deletes existing market, macro, or news rows. Bar/macro/news
rows are idempotent through their existing natural keys; the one latest-price
row per pair is replaced only when its exact synthetic timestamp is rerun.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import dotenv_values

try:
    from generate_fx_synthetic_excel import (
        DEFAULT_AS_OF,
        DEFAULT_DAILY_BARS,
        DEFAULT_HOURLY_BARS,
        DEFAULT_MACRO_VINTAGES,
        DEFAULT_NEWS_PER_PAIR,
        DEFAULT_PRICE_DAYS,
        PAIR_SPECS,
        build_workbook,
    )
except ImportError:  # pragma: no cover - supports package-style imports in tests
    from agent.scripts.generate_fx_synthetic_excel import (  # type: ignore
        DEFAULT_AS_OF,
        DEFAULT_DAILY_BARS,
        DEFAULT_HOURLY_BARS,
        DEFAULT_MACRO_VINTAGES,
        DEFAULT_NEWS_PER_PAIR,
        DEFAULT_PRICE_DAYS,
        PAIR_SPECS,
        build_workbook,
    )


PROVIDER = "LSEG"
EURUSD_INSTRUMENT_ID = "FX_EURUSD"
PAIR_INSTRUMENT_IDS = {
    "EURUSD": "FX_EURUSD",
    "GBPUSD": "FX_GBPUSD",
    "USDJPY": "FX_USDJPY",
    "AUDUSD": "FX_AUDUSD",
    "USDCAD": "FX_USDCAD",
    "USDCHF": "FX_USDCHF",
    "NZDUSD": "FX_NZDUSD",
}


def _parse_as_of(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    return parsed.astimezone(timezone.utc)


def _bar_rows(
    *,
    as_of: datetime,
    daily_bars: int,
    hourly_bars: int,
) -> list[tuple[Any, ...]]:
    """Create rows matching source.market_bars' physical column order."""

    rows: list[tuple[Any, ...]] = []
    for pair_index, (symbol, source_identifier, spot, drift, _country) in enumerate(
        PAIR_SPECS
    ):
        if symbol not in PAIR_INSTRUMENT_IDS:
            continue
        scale = max(abs(spot) * 0.0012, 0.0008)
        for index in range(daily_bars, 0, -1):
            close = (
                spot
                - 0.065 * scale
                + index * drift
                + math.sin((index + pair_index) / 4) * scale
            )
            previous = close - drift * 0.8
            stamp = as_of - timedelta(days=index)
            rows.append(
                (
                    stamp.date(),
                    stamp,
                    "daily",
                    previous,
                    close + scale * 0.7,
                    previous - scale * 0.6,
                    close,
                    0,
                    PROVIDER,
                    source_identifier,
                )
            )
        for index in range(hourly_bars, 0, -1):
            close = (
                spot
                - 0.02 * scale
                + (hourly_bars - index) * drift / 24
                + math.sin((index + pair_index) / 7) * scale * 0.45
            )
            previous = close - drift / 24
            stamp = as_of - timedelta(hours=index)
            rows.append(
                (
                    stamp.date(),
                    stamp,
                    "hourly",
                    previous,
                    close + scale * 0.22,
                    previous - scale * 0.18,
                    close,
                    0,
                    PROVIDER,
                    source_identifier,
                )
            )
    return rows


def _price_rows(as_of: datetime) -> list[tuple[Any, ...]]:
    """Create one current quote per registered pair."""

    stamp = as_of - timedelta(minutes=5)
    rows: list[tuple[Any, ...]] = []
    for _symbol, source_identifier, spot, _drift, _country in PAIR_SPECS:
        if source_identifier == "EURJPY=R":
            continue
        spread = max(abs(spot) * 0.00004, 0.00005)
        rows.append(
            (
                stamp,
                spot,
                spot - spread,
                spot + spread,
                spot,
                PROVIDER,
                source_identifier,
            )
        )
    return rows


def _macro_specs() -> tuple[tuple[str, str, float, str, float], ...]:
    """Return the 16 EURUSD-linked metric IDs already in the catalog."""

    return (
        ("EU_INTEREST_RATE", "EU", 2.75, "percent", 0.05),
        ("EU_PMI_MANUFACTURING", "EU", 50.8, "index", 0.4),
        ("EU_PMI_SERVICES", "EU", 51.2, "index", 0.35),
        ("EU_UNEMPLOYMENT", "EU", 6.2, "percent", 0.12),
        ("EU_CPI_YOY", "EU", 2.3, "percent", 0.08),
        ("EU_CORE_CPI_YOY", "EU", 2.0, "percent", 0.06),
        ("US_INTEREST_RATE", "US", 3.50, "percent", 0.05),
        ("US_PMI_MANUFACTURING", "US", 53.0, "index", 0.4),
        ("US_PMI_SERVICES", "US", 54.0, "index", 0.35),
        ("US_UNEMPLOYMENT", "US", 4.0, "percent", 0.12),
        ("US_CPI_YOY", "US", 2.5, "percent", 0.08),
        ("US_CORE_CPI_YOY", "US", 2.3, "percent", 0.06),
        ("US_CORE_PCE_YOY", "US", 2.6, "percent", 0.06),
        ("US_PCE_YOY", "US", 2.8, "percent", 0.06),
        ("US_NFP", "US", 180.0, "thousand_persons", 5.0),
        ("US_INDUSTRIAL_PRODUCTION", "US", 0.2, "percent", 0.05),
    )


def _macro_rows(as_of: datetime, vintages: int) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for metric_index, (metric_id, country, value, unit, surprise) in enumerate(
        _macro_specs()
    ):
        for vintage in range(vintages):
            release = as_of - timedelta(
                days=2 + metric_index % 6 + vintage * 30
            )
            actual = value - vintage * surprise * 0.35
            rows.append(
                (
                    metric_id,
                    EURUSD_INSTRUMENT_ID,
                    release,
                    "monthly",
                    actual,
                    actual - surprise * 2,
                    actual - surprise,
                    None,
                    PROVIDER,
                    metric_id,
                    country,
                    unit,
                )
            )
    return rows


def _news_rows(as_of: datetime, per_pair: int) -> list[tuple[Any, ...]]:
    templates = (
        "central bank outlook shapes relative-rate view",
        "data releases test the current market trend",
        "risk sentiment shifts cross-asset flows",
        "traders monitor upcoming macro catalysts",
        "options market prices a change in volatility",
        "investors reassess growth expectations",
        "regional inflation enters the policy debate",
        "strategists update the medium-term scenario",
    )
    rows: list[tuple[Any, ...]] = []
    counter = 1
    for symbol, _identifier, _spot, _drift, country in PAIR_SPECS:
        base, quote = symbol[:3], symbol[3:]
        for index in range(per_pair):
            publish_time = as_of - timedelta(hours=2 + index * 8)
            title = f"[SYNTHETIC TEST] {base}/{quote} {templates[index % len(templates)]}"
            related = {"query_tag": country, "pair": symbol, "synthetic_test": True}
            rows.append(
                (
                    f"SYNTH-DB-N{counter}",
                    PROVIDER,
                    publish_time,
                    title,
                    "en",
                    0.0,
                    json.dumps(related, ensure_ascii=False),
                    json.dumps(["EURUSD", "macro", "synthetic_test"]),
                    title,
                    "Synthetic test article; not a live market source.",
                )
            )
            counter += 1
    return rows


def _next_ids(cursor: Any, table: str, count: int) -> list[int]:
    cursor.execute(f"SELECT COALESCE(MAX(id), 0) FROM source.{table}")
    start = int(cursor.fetchone()[0])
    return [start + index for index in range(1, count + 1)]


def seed_database(
    *,
    as_of: datetime,
    daily_bars: int,
    hourly_bars: int,
    macro_vintages: int,
    news_per_pair: int,
    env_path: Path,
) -> dict[str, int]:
    """Insert synthetic records into the existing source schema."""

    import psycopg2
    from psycopg2.extras import execute_values

    values = {**dotenv_values(env_path), **os.environ}
    connection = psycopg2.connect(
        host=values.get("MARKET_DB_HOST", "127.0.0.1"),
        port=int(values.get("MARKET_DB_PORT", "15433")),
        dbname=values.get("MARKET_DB_NAME", "icbc_shared"),
        user=values.get("MARKET_DB_USER", "icbc_collab"),
        password=values.get("MARKET_DB_PASSWORD", ""),
    )
    counts: dict[str, int] = {}
    try:
        with connection:
            with connection.cursor() as cursor:
                prices = _price_rows(as_of)
                price_time = prices[0][0]
                # latest_prices has no natural-key constraint. The timestamp is
                # deterministic, so rerunning the same seed replaces only the
                # generated quote, never an unrelated live row.
                cursor.execute(
                    "DELETE FROM source.latest_prices "
                    "WHERE source = %s AND price_time = %s",
                    (PROVIDER, price_time),
                )
                price_ids = _next_ids(cursor, "latest_prices", len(prices))
                execute_values(
                    cursor,
                    "INSERT INTO source.latest_prices "
                    "(id, price_time, last, bid, ask, mid, source, source_identifier, updated_at) "
                    "VALUES %s",
                    [(price_ids[index], *row) for index, row in enumerate(prices)],
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, now())",
                )
                counts["latest_prices"] = len(prices)

                bars = _bar_rows(
                    as_of=as_of,
                    daily_bars=daily_bars,
                    hourly_bars=hourly_bars,
                )
                bar_ids = _next_ids(cursor, "market_bars", len(bars))
                execute_values(
                    cursor,
                    "INSERT INTO source.market_bars "
                    "(id, date, bar_time, frequency, open, high, low, close, volume, source, source_identifier, updated_at) "
                    "VALUES %s "
                    "ON CONFLICT (source_identifier, bar_time, frequency, source) DO UPDATE SET "
                    "date=EXCLUDED.date, open=EXCLUDED.open, high=EXCLUDED.high, "
                    "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, updated_at=now()",
                    [(bar_ids[index], *row) for index, row in enumerate(bars)],
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
                )
                counts["market_bars"] = len(bars)

                macro = _macro_rows(as_of, macro_vintages)
                macro_ids = _next_ids(cursor, "macro_observations", len(macro))
                execute_values(
                    cursor,
                    "INSERT INTO source.macro_observations "
                    "(id, metric_id, instrument_id, release_time, frequency, value, previous_value, "
                    "forecast_value, revised_value, source, source_identifier, country, unit, created_at, updated_at) "
                    "VALUES %s "
                    "ON CONFLICT (metric_id, release_time, frequency, source) DO UPDATE SET "
                    "instrument_id=EXCLUDED.instrument_id, value=EXCLUDED.value, "
                    "previous_value=EXCLUDED.previous_value, forecast_value=EXCLUDED.forecast_value, "
                    "revised_value=EXCLUDED.revised_value, source_identifier=EXCLUDED.source_identifier, "
                    "country=EXCLUDED.country, unit=EXCLUDED.unit, updated_at=now()",
                    [(macro_ids[index], *row) for index, row in enumerate(macro)],
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())",
                )
                counts["macro_observations"] = len(macro)

                news = _news_rows(as_of, news_per_pair)
                news_ids = _next_ids(cursor, "news_articles", len(news))
                execute_values(
                    cursor,
                    "INSERT INTO source.news_articles "
                    "(id, article_id, source, publish_time, title, language, sentiment_score, "
                    "related_entities, keywords, updated_at, content, summary) VALUES %s "
                    "ON CONFLICT (article_id, source) DO UPDATE SET "
                    "publish_time=EXCLUDED.publish_time, title=EXCLUDED.title, language=EXCLUDED.language, "
                    "sentiment_score=EXCLUDED.sentiment_score, related_entities=EXCLUDED.related_entities, "
                    "keywords=EXCLUDED.keywords, updated_at=now(), content=EXCLUDED.content, summary=EXCLUDED.summary",
                    [(news_ids[index], *row) for index, row in enumerate(news)],
                    template="(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now(), %s, %s)",
                )
                counts["news_articles"] = len(news)
    finally:
        connection.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("agent/outputs/fx-debate-synthetic/complete_multi_pair.xlsx"),
    )
    parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    parser.add_argument("--price-days", type=int, default=DEFAULT_PRICE_DAYS)
    parser.add_argument("--daily-bars", type=int, default=DEFAULT_DAILY_BARS)
    parser.add_argument("--hourly-bars", type=int, default=DEFAULT_HOURLY_BARS)
    parser.add_argument("--macro-vintages", type=int, default=DEFAULT_MACRO_VINTAGES)
    parser.add_argument("--news-per-pair", type=int, default=DEFAULT_NEWS_PER_PAIR)
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--confirm-test-data", action="store_true")
    parser.add_argument(
        "--env",
        type=Path,
        default=Path("agent/.env"),
        help="database environment file used only with --database",
    )
    args = parser.parse_args()
    if args.database and not args.confirm_test_data:
        parser.error("--database requires --confirm-test-data because rows are synthetic")
    as_of = _parse_as_of(args.as_of)
    output = build_workbook(
        args.output.expanduser().resolve(),
        as_of,
        price_days=args.price_days,
        daily_bars=args.daily_bars,
        hourly_bars=args.hourly_bars,
        macro_vintages=args.macro_vintages,
        news_per_pair=args.news_per_pair,
    )
    result: dict[str, Any] = {
        "path": str(output),
        "synthetic": True,
        "as_of": as_of.isoformat(),
        "excel": {
            "price_days": args.price_days,
            "daily_bars_per_pair": args.daily_bars,
            "hourly_bars_per_pair": args.hourly_bars,
            "macro_vintages": args.macro_vintages,
            "news_per_pair": args.news_per_pair,
        },
    }
    if args.database:
        result["database"] = seed_database(
            as_of=as_of,
            daily_bars=args.daily_bars,
            hourly_bars=args.hourly_bars,
            macro_vintages=args.macro_vintages,
            news_per_pair=args.news_per_pair,
            env_path=args.env.expanduser().resolve(),
        )
        result["database_provider"] = PROVIDER
        result["database_note"] = (
            "Rows are synthetic test data written under the existing LSEG catalog "
            "so MCP can resolve the registered identifiers."
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
