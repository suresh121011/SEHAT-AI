"""Read-back text, correction validation and prefill assembly (docs/12 §6). Pure functions.

Policy (council-reviewed):
- Every extracted candidate needs an explicit decision by the case reviewer (creator ANM or a
  medical officer). The patient can record but cannot confirm. Silence, timeouts, closed pages or a
  failed/unavailable spoken read-back never count as confirmation — there is no implicit outcome.
- `confirmed` is allowed only when the candidate has an engine-shaped value (or is display-only) AND
  carries no blocking flag (negation, uncertainty, earlier-time reference, several values, unparsed
  number modifier or split number, …). Anything else must be `corrected` (value entered explicitly),
  `rejected` or marked `unsure`.
- `rejected` ("wrong / not said") and `unsure` ("needs checking") both leave the field missing, which
  the rules engine already treats as needing human review. Nothing is ever defaulted.
- A confirmed value only *pre-fills*. The reviewer still submits triage through the existing endpoint,
  which re-validates it. A wrong value that a reviewer confirms can still under-triage: read-back
  reduces mis-transcription, it does not verify the measurement itself.
"""

from __future__ import annotations

from typing import Literal

from app.voice.extract import BOUNDS, F_RANGE

Outcome = Literal["confirmed", "corrected", "rejected", "unsure"]
PREFILL_FIELDS = ("temp", "spo2", "pulse", "resp_rate", "bp", "age", "pregnancy")
DISPLAY_ONLY_FIELDS = ("symptom_duration",)
CORRECTABLE_FIELDS = PREFILL_FIELDS

TEMPLATE_REVIEW_STATUS = {"en": "project_draft", "hi": "draft_unreviewed_translation", "or": "draft_unreviewed_translation"}

_LABELS = {
    "en": {"temp": "temperature", "spo2": "oxygen level", "pulse": "pulse", "resp_rate": "breathing rate", "bp": "blood pressure",
           "age": "age", "pregnancy": "pregnancy", "symptom_duration": "duration", "unassigned": "a number"},
    "hi": {"temp": "तापमान", "spo2": "ऑक्सीजन स्तर", "pulse": "नब्ज़", "resp_rate": "साँस की दर", "bp": "रक्तचाप",
           "age": "उम्र", "pregnancy": "गर्भावस्था", "symptom_duration": "अवधि", "unassigned": "एक संख्या"},
    "or": {"temp": "ତାପମାତ୍ରା", "spo2": "ଅକ୍ସିଜେନ ସ୍ତର", "pulse": "ନାଡି", "resp_rate": "ଶ୍ୱାସ ହାର", "bp": "ରକ୍ତଚାପ",
           "age": "ବୟସ", "pregnancy": "ଗର୍ଭାବସ୍ଥା", "symptom_duration": "ଅବଧି", "unassigned": "ଏକ ସଂଖ୍ୟା"},
}
_FRAME = {
    "en": ("I heard {label}: {value}.", "Is that correct?"),
    "hi": ("मैंने सुना {label}: {value}।", "क्या यह सही है?"),
    "or": ("ମୁଁ ଶୁଣିଲି {label}: {value}।", "ଏହା ଠିକ୍ କି?"),
}
_PREGNANT = {"en": ("pregnant", "not pregnant"), "hi": ("गर्भवती", "गर्भवती नहीं"), "or": ("ଗର୍ଭବତୀ", "ଗର୍ଭବତୀ ନୁହଁନ୍ତି")}
_UNIT_TEXT = {
    "en": {"percent": "%", "per_min": "/min", "mmhg": "mmHg", "years": "years", "months": "months", "weeks": "weeks", "days": "days", "hours": "hours"},
    "hi": {"percent": "%", "per_min": "/मिनट", "mmhg": "mmHg", "years": "साल", "months": "महीने", "weeks": "हफ़्ते", "days": "दिन", "hours": "घंटे"},
    "or": {"percent": "%", "per_min": "/ମିନିଟ", "mmhg": "mmHg", "years": "ବର୍ଷ", "months": "ମାସ", "weeks": "ସପ୍ତାହ", "days": "ଦିନ", "hours": "ଘଣ୍ଟା"},
}


def _num(v: float | None) -> str:
    if v is None:
        return "?"
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def display_value(field: str, raw_value: float | None, raw_value2: float | None, unit: str | None, normalized: dict | None, language: str = "en") -> str:
    """Human-readable value. Temperature shows the heard unit and the exact °C used (2 decimals shown,
    the unrounded value is what is stored and pre-filled)."""
    if field == "pregnancy":
        yes, no = _PREGNANT[language]
        return no if normalized == {"pregnant": False} else yes
    if field == "bp":
        return f"{_num(raw_value)}/{_num(raw_value2)} mmHg"
    if field == "temp":
        if unit == "f" and normalized:
            return f"{_num(raw_value)} °F = {normalized['temp_c']:.2f} °C"
        if unit == "c":
            return f"{_num(raw_value)} °C"
        return f"{_num(raw_value)} (unit unclear)"
    suffix = _UNIT_TEXT.get(language, _UNIT_TEXT["en"]).get(unit or "", "")
    return f"{_num(raw_value)} {suffix}".strip()


# For these, the parsed number is known to be incomplete: the read-back quotes the words that were
# heard instead of stating a number (e.g. never "39 °C" for "साढ़े उनतालीस").
QUOTE_HEARD_FLAGS = frozenset({"number_modifier_unparsed", "number_sequence_ambiguous"})


def readback_text(field: str, raw_value, raw_value2, unit, normalized, language: str, heard: str | None = None, flags=()) -> str:
    heard_frame, question = _FRAME[language]
    label = _LABELS[language].get(field, field)
    if heard and QUOTE_HEARD_FLAGS.intersection(flags):
        value = f"“{heard}”"
    else:
        value = display_value(field, raw_value, raw_value2, unit, normalized, language)
    return f"{heard_frame.format(label=label, value=value)} {question}"


# Flags that mean "what was heard may not be the current, stated value". A flagged value cannot be
# accepted with one click: the reviewer must enter the value explicitly (`corrected`) or leave it blank.
BLOCKING_FLAGS = frozenset({
    "negation", "uncertainty", "temporal_reference", "multiple_values",
    "number_modifier_unparsed", "number_sequence_ambiguous", "unit_unknown",
    "out_of_domain_range", "non_integer", "bp_order_invalid", "age_unit_months", "needs_assignment",
    "unit_unclear", "number_word_homograph",
    # SpO2 said with oxygen support: confirming only the number would drop the oxygen context, and a
    # reading on oxygen read as room air can under-triage. The reviewer enters it and records the oxygen.
    "oxygen_context",
    "context_unclear", "bp_shorthand_possible",
})


def can_confirm(field: str, normalized: dict | None, flags: list[str] | tuple[str, ...] = ()) -> bool:
    if BLOCKING_FLAGS.intersection(flags):
        return False
    return field in DISPLAY_ONLY_FIELDS or (field in PREFILL_FIELDS and normalized is not None)


class CorrectionInvalid(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _int_in(key: str, v: float | None) -> int:
    if v is None or not float(v).is_integer():
        raise CorrectionInvalid("integer_required")
    lo, hi = BOUNDS[key]
    if not lo <= v <= hi:
        raise CorrectionInvalid("out_of_range")
    return int(v)


def normalize_correction(field: str, value: float | None, value2: float | None, unit: str | None) -> dict:
    """Validate a reviewer's corrected value against the engine's own domain; return engine shape."""
    if field == "temp":
        if unit not in ("c", "f") or value is None:
            raise CorrectionInvalid("unit_required")
        if unit == "f" and not F_RANGE[0] <= value <= F_RANGE[1]:
            raise CorrectionInvalid("out_of_range")
        temp_c = (value - 32.0) * 5.0 / 9.0 if unit == "f" else float(value)
        lo, hi = BOUNDS["temp_c"]
        if not lo <= temp_c <= hi:
            raise CorrectionInvalid("out_of_range")
        return {"temp_c": temp_c}
    if field in ("spo2", "pulse", "resp_rate"):
        return {field: _int_in(field, value)}
    if field == "bp":
        sbp, dbp = _int_in("sbp", value), _int_in("dbp", value2)
        if dbp >= sbp:
            raise CorrectionInvalid("bp_order_invalid")
        return {"sbp": sbp, "dbp": dbp}
    if field == "age":
        if unit not in (None, "years"):
            raise CorrectionInvalid("years_required")
        return {"age_years": _int_in("age_years", value)}
    if field == "pregnancy":
        if value not in (0, 1):
            raise CorrectionInvalid("boolean_required")
        return {"pregnant": bool(value)}
    raise CorrectionInvalid("field_not_correctable")


def assemble_prefill(resolved: list[dict]) -> dict:
    """`resolved`: latest resolution per candidate, each {field, outcome, values: dict, source: dict}.
    Only confirmed/corrected values with an engine shape are used. Several different values for the
    same field → `conflicts` (no value chosen). Equal values from several sources are merged."""
    by_field: dict[str, list[dict]] = {}
    for r in resolved:
        if r["outcome"] not in ("confirmed", "corrected") or not r["values"]:
            continue
        by_field.setdefault(r["field"], []).append(r)
    values: dict[str, dict] = {}
    conflicts: dict[str, list[dict]] = {}
    for fld, rows in by_field.items():
        distinct = {tuple(sorted(r["values"].items())) for r in rows}
        if len(distinct) == 1:
            values[fld] = {"values": rows[0]["values"], "sources": [r["source"] for r in rows]}
        else:
            conflicts[fld] = [{"values": r["values"], "source": r["source"]} for r in rows]
    vitals: dict = {}
    triage_fields: dict = {}
    for fld, entry in values.items():
        for key, v in entry["values"].items():
            (triage_fields if key in ("age_years", "pregnant") else vitals)[key] = v
    return {"vitals": vitals, "fields": triage_fields, "values": values, "conflicts": conflicts}
