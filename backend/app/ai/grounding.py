"""Grounding: the non-bypassable check that every model value is supported by its cited source text
(docs/16 §4). Pure: no I/O.

For each item, every evidence entry must name a known segment, and its quote must occur in that
segment (case- and whitespace-insensitive). Then:
- numbers: the value (and value2) must occur as a number in the quote. Number words are parsed with
  the voice extractor's parser, so "one hundred and forty over ninety" grounds 140/90; "one forty" is
  deliberately not summed there, so it does not ground 140 and is dropped with a reason;
- text (complaint, onset, duration, symptom and medication names, dose, frequency): every word of the
  value with 3+ characters must occur in the quotes;
- red flags and the urgency suggestion: only the quotes are checked (their values are closed enums).
Anything that fails is dropped and listed with a reason; nothing is dropped silently.

Negation is reconciled against the quote, asymmetrically (council amendment): a mention counts as
negated only if the model says so AND the quote contains a negation cue. A disagreement in either
direction keeps the mention as present and flags it for human review, so an alarm is never removed by
a negation the source does not show.
"""

import re
from dataclasses import dataclass, field

from app.ai.schemas import CRITICAL_MEASUREMENTS, Evidence, ExtractionOutput
from app.privacy.pii import residual_hit
from app.voice.extract import NEGATION
from app.voice.extract import _fold as fold_length_preserving
from app.voice.extract import _numbers as parse_numbers

_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d)")
_NEGATION_RX = re.compile(r"(?<![a-z'])(?:" + "|".join(re.escape(t) for t in sorted(NEGATION, key=len, reverse=True) if t.isascii()) + r")(?![a-z'])")


def norm(text: str) -> str:
    return _WS.sub(" ", text.casefold()).strip()


@dataclass(frozen=True)
class Entry:
    """One grounded item from one pass, flattened for voting."""

    key: str  # e.g. "chief_complaint", "bp", "symptom:fever", "red_flag:chest_pain_acute_24h"
    kind: str  # text | measurement | symptom | medication | red_flag | urgency
    vote: object  # hashable, normalised value compared across passes
    value: object  # JSON-able display value
    evidence: tuple[Evidence, ...]
    critical: bool = False
    flags: tuple[str, ...] = ()


@dataclass
class Grounded:
    entries: list[Entry] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)


def _quotes_ok(segments: dict[str, str], evidence: list[Evidence]) -> str | None:
    for ev in evidence:
        seg = segments.get(ev.segment_id)
        if seg is None:
            return "segment_unknown"
        if norm(ev.quote) not in norm(seg):
            return "quote_not_in_source"
    return None


def _numbers_in(quote: str) -> list[float]:
    folded = fold_length_preserving(_THOUSANDS.sub("", quote))
    return [n.value for n in parse_numbers(folded)]


def _words_ok(value: str, evidence: list[Evidence]) -> bool:
    quoted = set(_WORD.findall(" ".join(norm(e.quote) for e in evidence)))
    return all(w in quoted for w in _WORD.findall(norm(value)) if len(w) >= 3)


def has_negation_cue(evidence: list[Evidence]) -> bool:
    return any(_NEGATION_RX.search(norm(e.quote)) for e in evidence)


def _negation(model_negated: bool, evidence: list[Evidence]) -> tuple[bool, tuple[str, ...]]:
    cue = has_negation_cue(evidence)
    if model_negated and cue:
        return True, ()
    if model_negated:
        return False, ("negation_unsupported",)
    if cue:
        return False, ("negation_conflict",)
    return False, ()


def _num_key(v: float) -> float:
    return round(float(v), 4)


def ground(segments: dict[str, str], out: ExtractionOutput) -> Grounded:
    """`segments` maps segment id → redacted segment text (exactly what the provider saw)."""
    g = Grounded()

    def drop(field_name: str, reason: str) -> None:
        g.dropped.append({"field": field_name, "reason": reason})

    def text_entry(key: str, kind: str, label: str, value_text: str, evidence: list[Evidence], vote, value, critical=False, flags=()) -> None:
        reason = _quotes_ok(segments, evidence)
        if reason is None and not _words_ok(value_text, evidence):
            reason = "value_not_in_quote"
        if reason is None and residual_hit(value_text):
            reason = "pii_pattern_in_output"
        if reason:
            drop(label, reason)
            return
        g.entries.append(Entry(key, kind, vote, value, tuple(evidence), critical, tuple(flags)))

    for name in ("chief_complaint", "onset", "duration"):
        item = getattr(out, name)
        if item is not None:
            text_entry(name, "text", name, item.value, item.evidence, norm(item.value), item.value)

    seen: dict[str, int] = {}
    for m in out.measurements:
        label = m.name
        reason = _quotes_ok(segments, m.evidence)
        if reason is None:
            nums = [n for e in m.evidence for n in _numbers_in(e.quote)]
            wanted = [m.value] + ([m.value2] if m.value2 is not None else [])
            if not all(any(abs(n - w) < 1e-6 for n in nums) for w in wanted):
                reason = "value_not_in_quote"
        if reason is None and m.unit and residual_hit(m.unit):
            reason = "pii_pattern_in_output"
        if reason:
            drop(label, reason)
            continue
        seen[m.name] = seen.get(m.name, 0) + 1
        key = m.name if seen[m.name] == 1 else f"{m.name}#{seen[m.name]}"
        vote = (_num_key(m.value), None if m.value2 is None else _num_key(m.value2), norm(m.unit or ""))
        value = {"value": m.value, "value2": m.value2, "unit": m.unit}
        g.entries.append(Entry(key, "measurement", vote, value, tuple(m.evidence), m.name in CRITICAL_MEASUREMENTS))

    for s in out.symptoms:
        negated, flags = _negation(s.negated, s.evidence)
        text_entry(f"symptom:{norm(s.name)}", "symptom", f"symptom:{norm(s.name)}", s.name, s.evidence, negated,
                   {"name": s.name, "negated": negated}, flags=flags)

    for med in out.medications:
        parts = " ".join(p for p in (med.name, med.dose, med.frequency) if p)
        vote = (norm(med.dose or ""), norm(med.frequency or ""))
        text_entry(f"medication:{norm(med.name)}", "medication", f"medication:{norm(med.name)}", parts, med.evidence, vote,
                   {"name": med.name, "dose": med.dose, "frequency": med.frequency})

    for rf in out.red_flags:
        key = f"red_flag:{rf.flag.value}"
        reason = _quotes_ok(segments, rf.evidence)
        if reason:
            drop(key, reason)
            continue
        negated, flags = _negation(rf.negated, rf.evidence)
        g.entries.append(Entry(key, "red_flag", negated, {"flag": rf.flag.value, "negated": negated}, tuple(rf.evidence), True, flags))

    if out.urgency_suggestion is not None:
        u = out.urgency_suggestion
        reason = _quotes_ok(segments, u.evidence)
        if reason:
            drop("urgency_suggestion", reason)
        else:
            g.entries.append(Entry("urgency_suggestion", "urgency", u.level, u.level, tuple(u.evidence), True))
    return g
