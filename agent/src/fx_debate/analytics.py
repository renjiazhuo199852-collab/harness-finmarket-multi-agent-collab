"""Deterministic point-in-time calculations for FX market evidence."""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any


# A short sample can be useful for an operator-facing observation, but it is
# not enough to confirm a trade regime.  Callers that need full confirmation
# keep the historical 50-bar default below.
TECHNICAL_OBSERVATION_MIN_BARS = 20
TECHNICAL_CONFIRMATION_MIN_BARS = 50


def normalize_bars(
    rows: list[dict[str, Any]], *, as_of: datetime
) -> list[dict[str, Any]]:
    """Normalize OHLC rows, exclude future data, and sort oldest first."""
    bars: list[dict[str, Any]] = []
    for row in rows:
        timestamp = as_utc_datetime(row.get("bar_time") or row.get("bar_date"))
        if timestamp > as_of:
            continue
        try:
            bar = {
                **row,
                "bar_time": timestamp,
                "open": _number(row["open"]),
                "high": _number(row["high"]),
                "low": _number(row["low"]),
                "close": _number(row["close"]),
                "volume": _number(row.get("volume") or 0),
            }
        except (KeyError, TypeError, ValueError):
            continue
        if bar["high"] < bar["low"] or not all(
            math.isfinite(bar[field]) for field in ("open", "high", "low", "close")
        ):
            continue
        bars.append(bar)
    return sorted(bars, key=lambda item: item["bar_time"])


def aggregate_four_hour(
    hourly_bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate ordered hourly OHLC rows into UTC four-hour buckets."""
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    for row in hourly_bars:
        timestamp = row["bar_time"]
        bucket = timestamp.replace(
            hour=(timestamp.hour // 4) * 4,
            minute=0,
            second=0,
            microsecond=0,
        )
        buckets.setdefault(bucket, []).append(row)

    aggregated: list[dict[str, Any]] = []
    for bucket, rows in sorted(buckets.items()):
        unique_hours = {
            row["bar_time"].replace(minute=0, second=0, microsecond=0) for row in rows
        }
        expected_hours = {bucket + timedelta(hours=offset) for offset in range(4)}
        if len(rows) != 4 or unique_hours != expected_hours:
            continue
        aggregated.append(
            {
                **rows[-1],
                "bar_time": rows[-1]["bar_time"],
                "bucket_start_time": bucket,
                "open": rows[0]["open"],
                "high": max(row["high"] for row in rows),
                "low": min(row["low"] for row in rows),
                "close": rows[-1]["close"],
                "volume": sum(row["volume"] for row in rows),
                "frequency": "4H",
            }
        )
    return aggregated


def technical_metrics(
    bars: list[dict[str, Any]],
    *,
    periods_per_year: int,
    min_bars: int = TECHNICAL_CONFIRMATION_MIN_BARS,
) -> dict[str, tuple[float, str | None]]:
    """Calculate indicators available for the supplied sample size.

    The default remains the full confirmation contract.  A lower ``min_bars``
    is only used by the evidence factory for an observation-level preview; any
    indicator whose lookback does not fit the sample is omitted instead of
    being calculated from an undersized window.
    """
    if len(bars) < min_bars:
        return {}
    closes = [row["close"] for row in bars]
    metrics: dict[str, tuple[float, str | None]] = {"latest_close": (closes[-1], None)}
    if len(closes) >= 6:
        metrics["return_5"] = (
            closes[-1] / closes[-6] - 1,
            "close[-1] / close[-6] - 1",
        )
    if len(closes) >= 21:
        metrics["return_20"] = (
            closes[-1] / closes[-21] - 1,
            "close[-1] / close[-21] - 1",
        )
    if len(closes) >= 20:
        metrics["ema_20"] = (_ema(closes, 20), "EMA(close, span=20)")
        metrics["high_20"] = (
            max(row["high"] for row in bars[-20:]),
            "max(high, 20)",
        )
        metrics["low_20"] = (
            min(row["low"] for row in bars[-20:]),
            "min(low, 20)",
        )
    if len(closes) >= 50:
        metrics["ema_50"] = (_ema(closes, 50), "EMA(close, span=50)")
    if len(closes) >= 15:
        metrics["rsi_14"] = (_rsi(closes, 14), "RSI(close, period=14)")
        metrics["atr_14"] = (_atr(bars, 14), "ATR(OHLC, period=14)")
    if len(closes) >= 21:
        metrics["realized_vol_20"] = (
            _realized_volatility(closes, 20, periods_per_year),
            f"stdev(log returns, 20) * sqrt({periods_per_year})",
        )
    return {
        name: (round(value, 10), calculation)
        for name, (value, calculation) in metrics.items()
    }


def as_utc_datetime(value: Any) -> datetime:
    """Convert supported PostgreSQL date/time values to timezone-aware UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"unsupported timestamp type: {type(value).__name__}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float:
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return float(str(value))


def _ema(values: list[float], period: int) -> float:
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _rsi(values: list[float], period: int) -> float:
    changes = [current - previous for previous, current in zip(values, values[1:])]
    recent = changes[-period:]
    average_gain = sum(max(change, 0) for change in recent) / period
    average_loss = sum(max(-change, 0) for change in recent) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)


def _atr(bars: list[dict[str, Any]], period: int) -> float:
    true_ranges: list[float] = []
    for previous, current in zip(bars, bars[1:]):
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    return sum(true_ranges[-period:]) / period


def _realized_volatility(
    values: list[float], period: int, periods_per_year: int
) -> float:
    returns = [
        math.log(current / previous)
        for previous, current in zip(values, values[1:])
        if previous > 0 and current > 0
    ][-period:]
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * math.sqrt(periods_per_year)
