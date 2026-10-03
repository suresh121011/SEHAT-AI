"""Model-facing extraction schema (docs/16 §3).

Every field is required-but-nullable (no defaults), so the same models serve Azure strict JSON-schema
output and server-side validation (`extra="forbid"`, `strict=True`). Every item must carry evidence:
a segment id and a verbatim quote from that segment. A reply that does not validate is an abstaining
MAKER pass; it is never read as free text.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.rules.models import AtpFlag

SCHEMA_VERSION = "extraction-2026-10-03.1"

MeasurementName = Literal[
    "temp", "spo2", "pulse", "resp_rate", "bp",  # vitals (bp: value = systolic, value2 = diastolic)
    "hb", "platelets", "creatinine", "troponin", "glucose",  # labs named in docs/09 §6.3
    "pain_severity",  # 0–10 self-report
]
# Values whose disagreement must never be resolved by majority (docs/09 §6.3, council amendment).
CRITICAL_MEASUREMENTS: frozenset[str] = frozenset({"temp", "spo2", "pulse", "resp_rate", "bp", "hb", "platelets", "creatinine", "troponin", "glucose"})

SEGMENT_ID = r"^S[0-9]{1,3}$"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Evidence(_M):
    segment_id: str = Field(pattern=SEGMENT_ID)
    quote: str = Field(min_length=1, max_length=300)


class TextItem(_M):
    value: str = Field(min_length=1, max_length=200)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class Measurement(_M):
    name: MeasurementName
    value: float
    value2: float | None
    unit: str | None = Field(max_length=20)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class Symptom(_M):
    name: str = Field(min_length=1, max_length=80)
    negated: bool
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class Medication(_M):
    name: str = Field(min_length=1, max_length=80)
    dose: str | None = Field(max_length=40)
    frequency: str | None = Field(max_length=40)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class RedFlagMention(_M):
    flag: AtpFlag
    negated: bool
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class UrgencySuggestion(_M):
    level: Literal["RED", "YELLOW", "GREEN"]
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class ExtractionOutput(_M):
    chief_complaint: TextItem | None
    onset: TextItem | None
    duration: TextItem | None
    symptoms: list[Symptom] = Field(max_length=30)
    measurements: list[Measurement] = Field(max_length=30)
    medications: list[Medication] = Field(max_length=20)
    red_flags: list[RedFlagMention] = Field(max_length=20)
    urgency_suggestion: UrgencySuggestion | None


_UNSUPPORTED_STRICT_KEYS = {"default", "title", "minLength", "maxLength", "pattern", "minItems", "maxItems", "minimum", "maximum",
                            "exclusiveMinimum", "exclusiveMaximum", "format", "description"}


def strict_json_schema(model: type[BaseModel]) -> dict:
    """JSON schema for provider structured output (strict mode): every object closed and every property
    required. Constraint keywords are removed for the provider; the server re-validates the full model."""

    def walk(node):
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_STRICT_KEYS}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(model.model_json_schema())
