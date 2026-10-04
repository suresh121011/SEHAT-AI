"""PII detection and redaction (docs/11 §A, §F). A heuristic risk-reduction control — NOT anonymization.

- Presidio *analyzer* only (presidio-anonymizer is excluded: it would downgrade `cryptography`);
  overlapping spans are merged and replaced here.
- Recognizers: Presidio phone (phonenumbers, IN region), email, IN_PAN (built-in, enabled), spaCy
  `en_core_web_sm` PERSON/LOCATION (weak for Indian names), plus heuristic pattern recognizers for
  Aadhaar-like, ABHA and Indian-mobile formats and full dates of birth. A format match is not proof
  that an identifier is valid; no identifier is verified.
- Fails closed: unsupported script, analyzer unavailable, internal error, or any residual identifier
  pattern after redaction → no RedactedText is produced.
- Raw text is only handled inside `_process_raw`, which returns a safe outcome (never raises), so the
  sanitized error is raised after that frame has unwound. This reduces how long raw text is referenced
  by exceptions/tracebacks; it does not guarantee no in-memory copy exists (the caller holds its own).
"""

import logging
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from app.errors import ApiError
from app.privacy.normalize import has_unsupported_script, normalize

for _name in ("presidio-analyzer", "presidio-anonymizer", "spacy"):
    logging.getLogger(_name).setLevel(logging.WARNING)

TOKENS = {
    "AADHAAR_LIKE": "[AADHAAR_REDACTED]",
    "ABHA_NUMBER": "[ABHA_REDACTED]",
    "ABHA_ADDRESS": "[ABHA_REDACTED]",
    "PHONE_NUMBER": "[PHONE_REDACTED]",
    "IN_MOBILE": "[PHONE_REDACTED]",
    "IN_PAN": "[PAN_REDACTED]",
    "EMAIL_ADDRESS": "[EMAIL_REDACTED]",
    "DOB": "[DATE_REDACTED]",
    "PERSON": "[PERSON_REDACTED]",
    "LOCATION": "[LOCATION_REDACTED]",
}
# Tie-break when overlapping spans have equal scores (most sensitive identifier wins the label).
_PRIORITY = ["AADHAAR_LIKE", "ABHA_NUMBER", "ABHA_ADDRESS", "PHONE_NUMBER", "IN_MOBILE", "IN_PAN", "EMAIL_ADDRESS", "DOB", "PERSON", "LOCATION"]
ENTITIES = list(TOKENS)
SCORE_THRESHOLD = 0.35

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# Heuristic patterns (see docs/11 recognizer table). Also reused for the post-redaction output check.
PATTERNS: dict[str, list[tuple[str, float]]] = {
    "AADHAAR_LIKE": [(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)", 0.7)],
    "ABHA_NUMBER": [(r"(?<!\d)\d{2}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)", 0.75)],
    "ABHA_ADDRESS": [(r"\b[a-z0-9._]{3,}@(?:abdm|sbx)\b", 0.85)],
    "IN_MOBILE": [(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)", 0.6)],
    "DOB": [
        (r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2}\b", 0.6),
        (r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b", 0.6),
        (rf"\b\d{{1,2}}\s+{_MONTHS}\s+(?:19|20)\d{{2}}\b", 0.6),
        (rf"\b{_MONTHS}\s+\d{{1,2}},?\s+(?:19|20)\d{{2}}\b", 0.6),
    ],
}
# Context-based name heuristics (case-sensitive): spaCy's small model misses many Indian names.
# Still heuristic: lowercase or unprefixed names can be missed (documented as xfail tests).
_NAME = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}"
NAME_PATTERNS: list[tuple[str, float]] = [
    (rf"(?<=\b(?:Mr|Mrs|Ms|Smt|Shri|Sri|Kumari|Dr)\.?\s){_NAME}", 0.7),
    (rf"(?<=\b(?:Patient|patient|Name|name|named)\s){_NAME}", 0.6),
    (rf"(?<=\bname is\s){_NAME}", 0.7),
    (rf"(?<=\b(?:son|daughter|wife|husband) of\s){_NAME}", 0.7),
]
_OUTPUT_CHECK = [re.compile(p, re.IGNORECASE) for plist in PATTERNS.values() for p, _ in plist]

# Residual sweep (fails closed): long digit runs with short separators, spelled-out number sequences.
_DIGIT_RUN = re.compile(r"\d(?:[^A-Za-z0-9\[\]]{0,3}\d){7,}")
_NUMBER_WORDS = {
    # English
    "zero", "oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    # romanized Hindi
    "shunya", "sunya", "ek", "do", "teen", "tin", "char", "chaar", "paanch", "panch", "chhe", "chhah", "che", "saat", "sat", "aath", "ath", "nau", "no",
}
_TOKEN = re.compile(r"[A-Za-z]+|\d+")
# Tokens a speech transcript (or an evasive writer) may place between digit groups. They do not break a
# digit run: "98765 dash 43210", "1234 gap 5678", "98765x43210", "double four".
_FILLERS = {"dash", "hyphen", "minus", "gap", "space", "blank", "point", "dot", "stop", "break", "comma", "slash", "and", "then", "x"}
_REPEATERS = {"double": 2, "triple": 3}
_RUN_DIGITS_LIMIT = 8
# Spelled-out numbers interrupted by ordinary words ("nine eight seven six five and then it continues
# four three two one zero"): once >= 3 number words are seen, a window tolerates up to _WORD_GAP other
# words before resetting.
_SPELLED_MIN_WORDS = 3
_WORD_GAP = 4
# Spoken-style emails ("a dot b at example dot com"): fail closed.
_SPOKEN_EMAIL = re.compile(
    r"\b[a-z0-9._-]+(?:\s+(?:dot|\.)\s+[a-z0-9_-]+)*\s+[\[(]?at[\])]?\s+[a-z0-9-]+(?:\s+[\[(]?dot[\])]?\s+[a-z0-9-]+)+\b",
    re.IGNORECASE,
)
_ABHA_DOMAIN = re.compile(r"@(?:abdm|sbx)\b", re.IGNORECASE)


class PiiRedactionError(ApiError):
    """Sanitized failure. Carries a reason code only — never text."""

    _MAP = {
        "unsupported_script": (422, "AI_INPUT_UNSUPPORTED_LANGUAGE", "AI assistance accepts English text only in this prototype"),
        "residual_identifier_pattern": (422, "PII_DETECTED", "Possible identifier remained after redaction; request blocked"),
        "redaction_unavailable": (503, "PII_REDACTION_UNAVAILABLE", "PII redaction is unavailable; request blocked"),
        "internal_error": (503, "PII_REDACTION_UNAVAILABLE", "PII redaction failed; request blocked"),
    }

    def __init__(self, reason_code: str):
        status, code, message = self._MAP.get(reason_code, self._MAP["internal_error"])
        super().__init__(status, code, message, {"reason_code": reason_code})
        self.reason_code = reason_code

    def __repr__(self) -> str:
        return f"PiiRedactionError({self.reason_code!r})"


_KEY = object()


class RedactedText:
    """The only type an LLM adapter accepts. Constructed only by this module."""

    __slots__ = ("text", "redacted_total")
    text: str
    redacted_total: int

    def __init__(self, key: object, text: str, redacted_total: int):
        if key is not _KEY:
            raise TypeError("RedactedText can only be produced by app.privacy.pii.redact")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "redacted_total", redacted_total)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("RedactedText is immutable")

    def __repr__(self) -> str:
        return f"RedactedText(redacted_total={self.redacted_total})"

    __str__ = __repr__


class _AnalyzerUnavailable(Exception):
    pass


@cache
def get_analyzer():
    """Build the Presidio analyzer once (en_core_web_sm; pattern + built-in recognizers)."""
    try:
        from presidio_analyzer import (
            AnalyzerEngine,
            Pattern,
            PatternRecognizer,
            RecognizerRegistry,
        )
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_analyzer.predefined_recognizers import (
            EmailRecognizer,
            InPanRecognizer,
            PhoneRecognizer,
            SpacyRecognizer,
        )

        nlp_engine = NlpEngineProvider(
            nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]}
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=["en"])
        registry.add_recognizer(SpacyRecognizer(supported_entities=["PERSON", "LOCATION"]))
        registry.add_recognizer(PhoneRecognizer(supported_regions=("IN", "US", "GB")))
        registry.add_recognizer(EmailRecognizer())
        registry.add_recognizer(InPanRecognizer())
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PERSON",
                name="ContextNameRecognizer",
                patterns=[Pattern(name=f"context_name_{i}", regex=p, score=s) for i, (p, s) in enumerate(NAME_PATTERNS)],
                global_regex_flags=re.DOTALL | re.MULTILINE,  # case-sensitive on purpose
            )
        )
        for entity, plist in PATTERNS.items():
            registry.add_recognizer(
                PatternRecognizer(
                    supported_entity=entity,
                    patterns=[Pattern(name=f"{entity.lower()}_{i}", regex=p, score=s) for i, (p, s) in enumerate(plist)],
                )
            )
        return AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=["en"], log_decision_process=False)
    except Exception:
        raise _AnalyzerUnavailable() from None


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    entity: str
    score: float


def merge_spans(spans: list[_Span]) -> list[_Span]:
    """Merge overlapping or touching spans into their union. Label = highest score; ties broken by
    _PRIORITY (most sensitive first)."""
    rank = {e: i for i, e in enumerate(_PRIORITY)}

    def better(a: _Span, b: _Span) -> _Span:
        if a.score != b.score:
            return a if a.score > b.score else b
        return a if rank.get(a.entity, 99) <= rank.get(b.entity, 99) else b

    merged: list[_Span] = []
    for s in sorted(spans, key=lambda x: (x.start, -x.end)):
        if merged and s.start <= merged[-1].end:
            cur = merged[-1]
            label = better(cur, s)
            merged[-1] = _Span(cur.start, max(cur.end, s.end), label.entity, label.score)
        else:
            merged.append(s)
    return merged


def apply_redactions(text: str, spans: list[_Span]) -> str:
    for s in sorted(spans, key=lambda x: x.start, reverse=True):
        text = text[: s.start] + TOKENS[s.entity] + text[s.end :]
    return text


def residual_hit(text: str) -> bool:
    """True if the redacted output still looks like it contains an identifier (fail closed)."""
    stripped = re.sub(r"\[[A-Z_]+_REDACTED\]", " ", text)
    if _DIGIT_RUN.search(stripped) or _ABHA_DOMAIN.search(stripped):
        return True
    if any(rx.search(stripped) for rx in _OUTPUT_CHECK):
        return True
    run = 0
    for tok in _TOKEN.findall(stripped):
        if tok.isdigit() or tok.lower() in _NUMBER_WORDS:
            run += 1
            if run >= 7:
                return True
        else:
            run = 0
    if _SPOKEN_EMAIL.search(stripped):
        return True
    return _digit_run_across_fillers(stripped) or _spelled_number_window(stripped)


def _spelled_number_window(text: str) -> bool:
    """Count digits across a spelled-number sequence that ordinary words interrupt. Digits written as
    numerals only count once the window is active (>= 3 number words), so labelled clinical vitals
    such as "BP 118/76, HR 110" do not trigger it."""
    words, digits, gap, multiplier = 0, 0, 0, 1
    for tok in _TOKEN.findall(text):
        low = tok.lower()
        if low in _NUMBER_WORDS:
            words += 1
            digits += multiplier
            gap, multiplier = 0, 1
        elif low in _REPEATERS:
            multiplier = _REPEATERS[low]
        elif tok.isdigit():
            if words >= _SPELLED_MIN_WORDS:
                digits += len(tok) * multiplier
                gap, multiplier = 0, 1
        elif low in _FILLERS or len(low) == 1:
            continue
        else:
            gap += 1
            if gap > _WORD_GAP:
                words, digits, gap, multiplier = 0, 0, 0, 1
        if words >= _SPELLED_MIN_WORDS and digits >= _RUN_DIGITS_LIMIT:
            return True
    return False


def _digit_run_across_fillers(text: str) -> bool:
    """Count digits across a run of digits / number words / filler tokens / single letters, with
    'double'/'triple' as multipliers. Any run of >= 8 digits fails closed. Over-blocking a long run of
    bare clinical numbers is accepted (fail-safe); ordinary labelled vitals are not affected because
    multi-letter labels (BP, HR, bpm, days) end a run."""
    digits, multiplier = 0, 1
    for tok in _TOKEN.findall(text):
        low = tok.lower()
        if tok.isdigit():
            digits += len(tok) * multiplier
            multiplier = 1
        elif low in _NUMBER_WORDS:
            digits += multiplier
            multiplier = 1
        elif low in _REPEATERS:
            multiplier = _REPEATERS[low]
        elif low in _FILLERS or len(low) == 1:
            continue
        else:
            digits, multiplier = 0, 1
        if digits >= _RUN_DIGITS_LIMIT:
            return True
    return False


@dataclass(frozen=True)
class _SafeOutcome:
    redacted: RedactedText | None
    reason: str | None


def _process_raw(raw: str) -> _SafeOutcome:
    """All raw-text handling happens here. Never raises: every failure becomes a reason code, and the
    exception object is not retained once the except block ends."""
    try:
        text = normalize(raw)
        if has_unsupported_script(text):
            return _SafeOutcome(None, "unsupported_script")
        analyzer = get_analyzer()
        results = analyzer.analyze(text=text, language="en", entities=ENTITIES, score_threshold=SCORE_THRESHOLD)
        spans = merge_spans([_Span(r.start, r.end, r.entity_type, r.score) for r in results])
        out = apply_redactions(text, spans)
        if residual_hit(out):
            return _SafeOutcome(None, "residual_identifier_pattern")
        return _SafeOutcome(RedactedText(_KEY, out, len(spans)), None)
    except _AnalyzerUnavailable:
        return _SafeOutcome(None, "redaction_unavailable")
    except Exception:
        return _SafeOutcome(None, "internal_error")


def redact(raw: str) -> RedactedText:
    """Redact `raw` or raise a sanitized PiiRedactionError (raised after the raw-handling frame returned)."""
    outcome = _process_raw(raw)
    del raw
    if outcome.redacted is None:
        raise PiiRedactionError(outcome.reason or "internal_error")
    return outcome.redacted


# ── Phase 6: segmented prompts (docs/16 §4) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RedactedSegment:
    """One redacted input segment. `replacements` maps each redaction token back to the input it replaced:
    (redacted_start, redacted_end, input_start, input_end). Input offsets index the *normalized* text; they
    equal raw-text offsets only when `normalization_identity` is true (always so for plain ASCII)."""

    segment_id: str
    text: str
    redacted_total: int
    replacements: tuple[tuple[int, int, int, int], ...]
    normalization_identity: bool

    def __repr__(self) -> str:
        return f"RedactedSegment({self.segment_id!r}, redacted_total={self.redacted_total})"

    __str__ = __repr__

    def input_offsets(self, start: int, end: int) -> tuple[int, int] | None:
        """Map a range of the redacted text to the raw input range, or None if normalization changed the
        text (offsets would not index the raw input). A range touching a token widens to the whole token."""
        if not self.normalization_identity:
            return None

        def m(p: int, is_end: bool) -> int:
            shift = 0
            for rs, re_, ns, ne in self.replacements:
                if p <= rs:
                    return p + shift
                if p < re_:
                    return ne if is_end else ns
                shift = ne - re_
            return p + shift

        return m(start, False), m(end, True)


class RedactedPrompt:
    """The only input a Phase 6 structured provider accepts. Constructed only by `redact_segments`."""

    __slots__ = ("segments",)
    segments: tuple[RedactedSegment, ...]

    def __init__(self, key: object, segments: tuple[RedactedSegment, ...]):
        if key is not _KEY:
            raise TypeError("RedactedPrompt can only be produced by app.privacy.pii.redact_segments")
        object.__setattr__(self, "segments", segments)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("RedactedPrompt is immutable")

    @property
    def redacted_total(self) -> int:
        return sum(s.redacted_total for s in self.segments)

    def segment_texts(self) -> dict[str, str]:
        return {s.segment_id: s.text for s in self.segments}

    def __repr__(self) -> str:
        return f"RedactedPrompt(segments={len(self.segments)}, redacted_total={self.redacted_total})"

    __str__ = __repr__


@dataclass(frozen=True)
class _SafeSegmentsOutcome:
    prompt: RedactedPrompt | None
    reason: str | None


def _process_segments(raw_segments: list[tuple[str, str]]) -> _SafeSegmentsOutcome:
    """Redact each (segment_id, raw text) independently. Any failing segment fails the whole prompt closed.
    Never raises (same discipline as `_process_raw`)."""
    try:
        out: list[RedactedSegment] = []
        for segment_id, raw in raw_segments:
            text = normalize(raw)
            if has_unsupported_script(text):
                return _SafeSegmentsOutcome(None, "unsupported_script")
            analyzer = get_analyzer()
            results = analyzer.analyze(text=text, language="en", entities=ENTITIES, score_threshold=SCORE_THRESHOLD)
            spans = merge_spans([_Span(r.start, r.end, r.entity_type, r.score) for r in results])
            redacted = apply_redactions(text, spans)
            if residual_hit(redacted):
                return _SafeSegmentsOutcome(None, "residual_identifier_pattern")
            reps, delta = [], 0
            for s in sorted(spans, key=lambda x: x.start):
                token = TOKENS[s.entity]
                rs = s.start + delta
                reps.append((rs, rs + len(token), s.start, s.end))
                delta += len(token) - (s.end - s.start)
            out.append(RedactedSegment(segment_id, redacted, len(spans), tuple(reps), text == raw))
        return _SafeSegmentsOutcome(RedactedPrompt(_KEY, tuple(out)), None)
    except _AnalyzerUnavailable:
        return _SafeSegmentsOutcome(None, "redaction_unavailable")
    except Exception:
        return _SafeSegmentsOutcome(None, "internal_error")


def redact_segments(raw_segments: list[tuple[str, str]]) -> RedactedPrompt:
    """Redact segments or raise a sanitized PiiRedactionError."""
    outcome = _process_segments(raw_segments)
    del raw_segments
    if outcome.prompt is None:
        raise PiiRedactionError(outcome.reason or "internal_error")
    return outcome.prompt
