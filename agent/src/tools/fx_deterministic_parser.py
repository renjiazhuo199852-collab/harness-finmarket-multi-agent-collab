"""Deterministic FX intent, pair, horizon, and chart-timeframe extraction.

The parser is deliberately offline and conservative.  It produces candidates
and ambiguity flags; the route adapter decides whether a candidate is safe to
launch as a five-agent debate.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.tools.fx_debate_contract import ALLOWED_FX_SYMBOLS
from src.tools.fx_nl_parser_contract import (
    FxNaturalLanguageParse,
    FxParsedIntent,
    FxParserResult,
    ParseSource,
)

_ALLOWED_SYMBOLS = frozenset(ALLOWED_FX_SYMBOLS)
_PAIR_BY_CURRENCIES = {
    frozenset(symbol[i : i + 3] for i in (0, 3)): symbol
    for symbol in ALLOWED_FX_SYMBOLS
}
_CURRENCY_CODES = frozenset(
    code for symbol in ALLOWED_FX_SYMBOLS for code in (symbol[:3], symbol[3:])
)
_RMB_GENERIC = "RMB_GENERIC"
_SIX_LETTER_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{6})(?![A-Z0-9])")
_SEPARATED_PAIR_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{3})\s*(?:/|-|_|VS|VERSUS)\s*([A-Z]{3})(?![A-Z0-9])"
)
_ISO_DURATION_RE = re.compile(r"(?<![A-Z0-9])(P(?:[1-9]\d*[DWMY]|T[1-9]\d*[HM]))(?![A-Z0-9])")
_AMBIGUITY_ORDER = (
    "cny_cnh_ambiguous",
    "multiple_fx_pairs",
    "conflicting_horizon",
    "conflicting_timeframes",
    "unsupported_fx_pair",
)

_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            (alias, code)
            for code, aliases in {
                "USD": ("USD", "美元", "美金"),
                "EUR": ("EUR", "欧元"),
                "GBP": ("GBP", "英镑"),
                "JPY": ("JPY", "日元", "日币"),
                "CHF": ("CHF", "瑞郎", "瑞士法郎"),
                "CAD": ("CAD", "加元", "加拿大元"),
                "AUD": ("AUD", "澳元"),
                "NZD": ("NZD", "纽元"),
                "CNY": ("CNY", "在岸人民币", "人民币在岸"),
                "CNH": ("CNH", "CNH", "离岸人民币", "人民币离岸"),
                _RMB_GENERIC: ("RMB", "人民币"),
            }.items()
            for alias in aliases
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


@dataclass(frozen=True)
class PairExtractionResult:
    canonical_symbols: tuple[str, ...]
    currency_mentions: tuple[str, ...]
    source: ParseSource
    ambiguity_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DurationExtractionResult:
    value: str | None
    source: ParseSource
    ambiguity_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimeframeExtractionResult:
    values: tuple[str, ...]
    source: ParseSource
    ambiguity_flags: tuple[str, ...] = ()


class DeterministicFxNaturalLanguageParser:
    """Async-compatible facade matching the upstream parser seam."""

    async def parse(self, prompt: str) -> FxParserResult:
        if not isinstance(prompt, str) or not prompt.strip():
            return FxParserResult("failed", None, "FX_PARSER_INPUT_INVALID")
        text = normalize_fx_text(prompt)
        pair = extract_fx_pair_candidates(text)
        horizon = extract_decision_horizon(text)
        timeframes = extract_analysis_timeframes(text)
        parsed = FxNaturalLanguageParse(
            intent_candidate=classify_fx_intent(text),
            symbol_candidates=pair.canonical_symbols,
            decision_horizon_candidate=horizon.value,
            analysis_timeframes_candidate=timeframes.values,
            symbol_source=pair.source,
            horizon_source=horizon.source,
            timeframes_source=timeframes.source,
            ambiguity_flags=_ordered_flags(
                pair.ambiguity_flags
                + horizon.ambiguity_flags
                + timeframes.ambiguity_flags
            ),
        )
        return FxParserResult("succeeded", parsed)


def normalize_fx_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    for source, target in {
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "（": "(",
        "）": ")",
        "、": " ",
        "\u3000": " ",
    }.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"\s+", " ", normalized).strip().upper()


def classify_fx_intent(text: str) -> FxParsedIntent:
    """Apply high-precision precedence so quote requests do not become debates."""
    if _has_live_execution(text):
        return FxParsedIntent.live_execution
    if any(keyword in text for keyword in ("当前汇率", "实时汇率", "现价", "报价", "CURRENT RATE", "SPOT RATE")):
        return FxParsedIntent.quote
    if any(keyword in text for keyword in ("兑换", "换成", "CONVERT", "EXCHANGE AMOUNT")):
        return FxParsedIntent.conversion
    if any(keyword in text for keyword in ("历史价格", "K线", "蜡烛图", "波动率", "市场数据", "CANDLES", "VOLATILITY")):
        return FxParsedIntent.market_data
    if any(keyword in text for keyword in ("回测", "BACKTEST")):
        return FxParsedIntent.backtest
    if any(keyword in text for keyword in ("解释", "什么是", "原理", "机制", "EXPLAIN", "WHAT IS")):
        return FxParsedIntent.explanation
    if any(
        keyword in text
        for keyword in (
            "DEBATE",
            "SWARM",
            "多空辩论",
            "外汇辩论",
            "FX辩论",
            "五 AGENT",
            "五智能体",
            "辩论模型",
        )
    ):
        return FxParsedIntent.debate
    if any(
        keyword in text
        for keyword in (
            "走势",
            "趋势",
            "方向",
            "涨跌",
            "看多",
            "看空",
            "升值",
            "贬值",
            "交易建议",
            "分析",
            "ANALYZE",
            "ANALYSIS",
            "OUTLOOK",
            "TREND",
            "BULLISH",
            "BEARISH",
            "FORECAST",
            "PREDICT",
        )
    ):
        return FxParsedIntent.directional
    if any(keyword in text for keyword in ("对冲", "套保", "HEDGE", "HEDGING", "EXPOSURE")):
        return FxParsedIntent.hedge
    if any(keyword in text for keyword in ("总结", "摘要", "概括", "SUMMARIZE")):
        return FxParsedIntent.summary
    return FxParsedIntent.unknown


def extract_fx_pair_candidates(text: str) -> PairExtractionResult:
    symbols: list[str] = []
    flags: list[str] = []
    explicit = False
    normalized = False

    for match in _SIX_LETTER_RE.finditer(text):
        token = match.group(1)
        symbol = _resolve_symbol(token[:3], token[3:])
        if symbol is None:
            continue
        symbols.append(symbol)
        explicit = explicit or token in _ALLOWED_SYMBOLS
        normalized = normalized or token != symbol

    for match in _SEPARATED_PAIR_RE.finditer(text):
        symbol = _resolve_symbol(match.group(1), match.group(2))
        if symbol is None:
            flags.append("unsupported_fx_pair")
            continue
        symbols.append(symbol)
        normalized = True

    mentions = _currency_mentions(text)
    for index, left in enumerate(mentions):
        for right in mentions[index + 1 :]:
            if right[1] <= left[1] or not _is_pair_connector(text[left[2] : right[1]]):
                continue
            resolved = _resolve_currency_pair(left[0], right[0])
            symbols.extend(resolved)
            normalized = normalized or bool(resolved)
            break

    canonical = _dedupe(symbols)
    if {"USDCNY", "USDCNH"}.issubset(canonical):
        flags.append("cny_cnh_ambiguous")
    elif len(canonical) > 1:
        flags.append("multiple_fx_pairs")
    ordered = _ordered_flags(tuple(flags))
    source = (
        ParseSource.ambiguous
        if ordered
        else ParseSource.explicit
        if canonical and explicit and not normalized
        else ParseSource.normalized
        if canonical
        else ParseSource.missing
    )
    return PairExtractionResult(
        canonical_symbols=canonical,
        currency_mentions=tuple(item[0] for item in mentions),
        source=source,
        ambiguity_flags=ordered,
    )


def extract_decision_horizon(text: str) -> DurationExtractionResult:
    matches: list[tuple[str, ParseSource]] = []
    explicit = re.findall(r"DECISION[_ ]?HORIZON\s*=\s*(P[1-9]\d*[DWMY])", text)
    matches.extend((value, ParseSource.explicit) for value in explicit)
    for match in _ISO_DURATION_RE.finditer(text):
        value = match.group(1)
        if value.startswith("PT") or "ANALYSIS_TIMEFRAME" in text[max(0, match.start() - 28) : match.start()]:
            continue
        if value not in {item[0] for item in matches}:
            matches.append((value, ParseSource.explicit))
    human_pattern = re.compile(
        r"(?:未来|接下来|NEXT)?\s*(两|二|一|三|[1-9]\d*)\s*(天|日|周|星期|个?月|年)|"
        r"(?:NEXT\s+)?([1-9]\d*|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\s*"
        r"(DAYS?|WEEKS?|MONTHS?|YEARS?)",
        re.IGNORECASE,
    )
    for match in human_pattern.finditer(text):
        number = match.group(1) or match.group(3)
        unit = match.group(2) or (match.group(4) or "").upper()
        value = _duration_from_text(number, unit)
        if value is not None:
            matches.append((value, ParseSource.normalized))
    unique = _dedupe(item[0] for item in matches)
    if not unique:
        return DurationExtractionResult(None, ParseSource.missing)
    if len(unique) > 1:
        return DurationExtractionResult(None, ParseSource.ambiguous, ("conflicting_horizon",))
    source = next(source for value, source in matches if value == unique[0])
    return DurationExtractionResult(unique[0], source)


def extract_analysis_timeframes(text: str) -> TimeframeExtractionResult:
    sections = re.findall(r"ANALYSIS[_ ]?TIMEFRAMES?\s*=\s*([^;；]+)", text)
    values: list[str] = []
    for section in sections:
        values.extend(_normalize_timeframe_token(item) for item in re.split(r"[,/、 ]+", section) if item.strip())
    patterns = (
        (r"4\s*小时|(?<![A-Z0-9])4H(?![A-Z0-9])", "PT4H"),
        (r"1\s*小时|(?<![A-Z0-9])1H(?![A-Z0-9])", "PT1H"),
        (r"日线|DAILY|(?<![A-Z0-9])1D(?![A-Z0-9])", "P1D"),
        (r"周线|WEEKLY|(?<![A-Z0-9])1W(?![A-Z0-9])", "P1W"),
    )
    for pattern, value in patterns:
        for _match in re.finditer(pattern, text):
            values.append(value)
    values = list(_dedupe(value for value in values if value))
    if not values:
        return TimeframeExtractionResult((), ParseSource.missing)
    if len(sections) > 1:
        section_values = [
            tuple(_dedupe(_normalize_timeframe_token(item) for item in re.split(r"[,/、 ]+", section) if item.strip()))
            for section in sections
        ]
        if len(set(section_values)) > 1:
            return TimeframeExtractionResult((), ParseSource.ambiguous, ("conflicting_timeframes",))
    return TimeframeExtractionResult(tuple(values), ParseSource.normalized)


def _currency_mentions(text: str) -> list[tuple[str, int, int]]:
    mentions: list[tuple[str, int, int]] = []
    occupied: list[tuple[int, int]] = []
    for alias, code in _ALIASES:
        # Three-letter codes must be standalone tokens. Without boundaries,
        # crypto symbols such as BTCUSDT or ETHUSD expose a false USD mention
        # and can be misrouted into the FX clarification branch.
        pattern = (
            rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])"
            if alias.isascii() and alias.isalnum()
            else re.escape(alias)
        )
        for match in re.finditer(pattern, text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            occupied.append(match.span())
            mentions.append((code, match.start(), match.end()))
    return sorted(mentions, key=lambda item: item[1])


def _resolve_currency_pair(left: str, right: str) -> tuple[str, ...]:
    if _RMB_GENERIC in {left, right}:
        if {left, right} == {_RMB_GENERIC, "USD"}:
            return ("USDCNY", "USDCNH")
        return ()
    symbol = _resolve_symbol(left, right)
    return (symbol,) if symbol else ()


def _resolve_symbol(base: str, quote: str) -> str | None:
    base, quote = base.upper(), quote.upper()
    if base == quote or base not in _CURRENCY_CODES or quote not in _CURRENCY_CODES:
        return None
    return _PAIR_BY_CURRENCIES.get(frozenset((base, quote)))


def _is_pair_connector(value: str) -> bool:
    compact = re.sub(r"\s+", "", value).upper()
    return not compact or compact in {"/", "-", "_", "VS", "VERSUS", "兑", "对", "和", "与"}


def _normalize_timeframe_token(value: str) -> str:
    token = value.strip().upper()
    aliases = {"4H": "PT4H", "1H": "PT1H", "1D": "P1D", "1W": "P1W"}
    return aliases.get(token, token)


def _duration_from_text(number: str, unit: str) -> str | None:
    numbers = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "ONE": 1,
        "TWO": 2,
        "THREE": 3,
        "FOUR": 4,
        "FIVE": 5,
        "SIX": 6,
        "SEVEN": 7,
        "EIGHT": 8,
        "NINE": 9,
        "TEN": 10,
    }
    try:
        count = numbers[number] if number in numbers else int(number)
    except (TypeError, ValueError):
        return None
    unit = unit.upper()
    if unit.startswith(("DAY", "日", "天")):
        suffix = "D"
    elif unit.startswith(("WEEK", "星期", "周")):
        suffix = "W"
    elif unit.startswith("MONTH") or "月" in unit:
        suffix = "M"
    elif unit.startswith(("YEAR", "年")):
        suffix = "Y"
    else:
        return None
    return f"P{count}{suffix}"


def _has_live_execution(text: str) -> bool:
    return any(word in text for word in ("立即买入", "立即卖出", "下单", "PLACE ORDER", "BUY NOW", "SELL NOW"))


def _dedupe(values) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _ordered_flags(flags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(flag for flag in _AMBIGUITY_ORDER if flag in set(flags))
