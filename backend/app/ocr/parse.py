"""Deterministic grammar for printed result, unit, range and flag text (docs/14 §5).

Never repairs: letters inside digits, comma decimals, two decimal points or merged numbers keep their
raw text, get `value=None` and a flag. Decimal values are kept as exact text (no float round-trip).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.ocr.lexicon import FLAG_TOKENS, QUALITATIVE, norm_key, unit_key

Comparator = Literal["<", "<=", ">", ">="]
_COMP = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">=", "=<": "<=", "=>": ">="}

_WESTERN = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?$")
_PLAIN = re.compile(r"^\d+(?:\.\d+)?$")
_CONFUSABLE = re.compile(r"[OoIlSB|]")


def _clean(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).strip()
    return t.replace("−", "-").replace("–", "-").replace("—", "-")


@dataclass(frozen=True)
class ParsedValue:
    raw: str
    kind: Literal["numeric", "qualitative", "unreadable", "empty"]
    value: str | None = None  # exact decimal text, thousands separators removed
    comparator: Comparator | None = None
    qualitative: str | None = None
    flags: tuple[str, ...] = ()


def _decimal_text(token: str) -> str | None:
    """Exact decimal text for a printed number, or None if the token is not unambiguously a number."""
    neg = token.startswith("-")
    body = token[1:] if neg else token
    if _PLAIN.match(body):
        out = body
    elif _WESTERN.match(body) or _INDIAN.match(body):
        out = body.replace(",", "")
    else:
        return None
    return ("-" if neg else "") + out


def parse_value(raw: str, *, allow_negative: bool = False) -> ParsedValue:
    t = _clean(raw)
    if not t:
        return ParsedValue(raw, "empty")
    qual = QUALITATIVE.get(norm_key(t))
    if qual:
        return ParsedValue(raw, "qualitative", qualitative=qual)
    comparator = None
    m = re.match(r"^(<=|>=|=<|=>|≤|≥|<|>)\s*", t)
    if m:
        comparator = _COMP[m.group(1)]
        t = t[m.end():]
    t = t.replace(" ", "")
    if t.startswith("-") and not allow_negative:
        return ParsedValue(raw, "unreadable", comparator=comparator, flags=("negative_not_expected",))
    value = _decimal_text(t)
    if value is not None:
        return ParsedValue(raw, "numeric", value=value, comparator=comparator)
    flags: list[str] = []
    if any(ch.isdigit() for ch in t) and _CONFUSABLE.search(t):
        flags.append("ocr_char_confusion")  # e.g. "1O.5", "8S"
    if re.fullmatch(r"-?\d+,\d{1,2}", t):
        flags.append("comma_decimal_ambiguous")  # "1,5": decimal comma or a dropped digit?
    if t.count(".") > 1:
        flags.append("multiple_decimal_points")
    if not flags:
        flags.append("value_unparsed")
    return ParsedValue(raw, "unreadable", comparator=comparator, flags=tuple(flags))


@dataclass(frozen=True)
class ParsedUnit:
    raw: str
    key: str | None
    flags: tuple[str, ...] = ()


def parse_unit(raw: str) -> ParsedUnit:
    t = _clean(raw)
    if not t:
        return ParsedUnit(raw, None, ("unit_missing",))
    key = unit_key(t)
    return ParsedUnit(raw, key, () if key else ("unit_unknown",))


@dataclass(frozen=True)
class ParsedRange:
    raw: str
    kind: Literal["between", "upper", "lower", "qualitative", "multi", "malformed", "none"]
    low: str | None = None
    high: str | None = None
    low_inclusive: bool = True
    high_inclusive: bool = True
    unit_key: str | None = None
    qualitative: str | None = None
    flags: tuple[str, ...] = field(default=())


_NUM = r"\d[\d,]*(?:\.\d+)?"


def parse_range(raw: str) -> ParsedRange:
    t = _clean(raw)
    if not t:
        return ParsedRange(raw, "none")
    qual = QUALITATIVE.get(norm_key(t))
    if qual:
        return ParsedRange(raw, "qualitative", qualitative=qual)
    low_t = t.lower()
    if re.search(r"\b(male|female|men|women|m\s*:|f\s*:|adult|child|children|pregnan|years|yrs)\b", low_t) or t.count(":") >= 2:
        return ParsedRange(raw, "multi", flags=("range_population_specific",))
    # Optional trailing unit inside the range cell, e.g. "13 - 17 g/dL".
    unit = None
    m_unit = re.match(rf"^(.*{_NUM})\s+([^\d\s].*)$", t)
    if m_unit and unit_key(m_unit.group(2)):
        t, unit = m_unit.group(1), unit_key(m_unit.group(2))
    m = re.fullmatch(rf"({_NUM})\s*(?:-|to)\s*({_NUM})", t, flags=re.IGNORECASE)
    if m:
        lo, hi = _decimal_text(m.group(1)), _decimal_text(m.group(2))
        if lo is None or hi is None:
            return ParsedRange(raw, "malformed", flags=("range_number_unparsed",))
        if Decimal(lo) >= Decimal(hi):
            return ParsedRange(raw, "malformed", low=lo, high=hi, unit_key=unit, flags=("range_inverted",))
        return ParsedRange(raw, "between", low=lo, high=hi, unit_key=unit)
    m = re.fullmatch(rf"(<=|≤|<|up\s*to|upto)\s*({_NUM})", t, flags=re.IGNORECASE)
    if m:
        hi = _decimal_text(m.group(2))
        if hi is None:
            return ParsedRange(raw, "malformed", flags=("range_number_unparsed",))
        return ParsedRange(raw, "upper", high=hi, high_inclusive=m.group(1) not in ("<",), unit_key=unit)
    m = re.fullmatch(rf"(>=|≥|>)\s*({_NUM})", t)
    if m:
        lo = _decimal_text(m.group(2))
        if lo is None:
            return ParsedRange(raw, "malformed", flags=("range_number_unparsed",))
        return ParsedRange(raw, "lower", low=lo, low_inclusive=m.group(1) != ">", unit_key=unit)
    return ParsedRange(raw, "malformed", flags=("range_unparsed",))


def parse_flag(raw: str) -> str | None:
    return FLAG_TOKENS.get(_clean(raw).lower()) if raw else None


RangeStatus = Literal["below_range", "within_range", "above_range", "range_unavailable", "not_comparable"]


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError):
        return None


def compare_to_range(value: ParsedValue, unit: str | None, rng: ParsedRange) -> RangeStatus:
    """Deterministic comparison of a printed result with a printed (or sourced) range. Conservative:
    anything that cannot be compared exactly is `not_comparable`, never guessed."""
    if rng.kind == "none":
        return "range_unavailable"
    if rng.kind in ("multi", "malformed"):
        return "not_comparable"
    if rng.kind == "qualitative" or value.kind == "qualitative":
        if rng.kind == "qualitative" and value.kind == "qualitative":
            return "within_range" if value.qualitative == rng.qualitative else "not_comparable"
        return "not_comparable"
    if value.kind != "numeric" or value.value is None:
        return "not_comparable"
    if unit is None:
        return "not_comparable"  # missing or unrecognised unit: a number alone cannot be placed against a range
    if rng.unit_key and rng.unit_key != unit:
        return "not_comparable"
    v = _dec(value.value)
    lo = _dec(rng.low) if rng.low else None
    hi = _dec(rng.high) if rng.high else None
    if v is None:
        return "not_comparable"
    comp = value.comparator
    # Comparator results ("<0.01") are only placed when the whole possible interval is on one side.
    if comp in ("<", "<="):
        # true value t satisfies t < v (or t <= v)
        if lo is not None:
            below = v <= lo if comp == "<" else (v < lo or (v == lo and not rng.low_inclusive))
            return "below_range" if below else "not_comparable"
        if hi is not None:  # upper-only range: within if every possible t is inside
            inside = v <= hi if (comp == "<" or rng.high_inclusive) else v < hi
            return "within_range" if inside else "not_comparable"
        return "not_comparable"
    if comp in (">", ">="):
        if hi is not None:
            above = v >= hi if comp == ">" else (v > hi or (v == hi and not rng.high_inclusive))
            return "above_range" if above else "not_comparable"
        if lo is not None:
            inside = v >= lo if (comp == ">" or rng.low_inclusive) else v > lo
            return "within_range" if inside else "not_comparable"
        return "not_comparable"
    if lo is not None and (v < lo or (v == lo and not rng.low_inclusive)):
        return "below_range"
    if hi is not None and (v > hi or (v == hi and not rng.high_inclusive)):
        return "above_range"
    return "within_range"
