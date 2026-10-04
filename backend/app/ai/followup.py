"""Missing information and follow-up questions for an extraction (docs/09 §6.4–§6.5). Deterministic.

A value counts as present only if it has a single value a human has not rejected: reviewer-accepted or
corrected, or MAKER-agreed/majority (still awaiting review), or a reviewed document value. Disputed values
count as missing. Red-flag screen completion is never inferred from text: it is always asked unless a human
records it on the triage form.
"""

from app.ai import question_bank
from app.rules.required_fields import missing


def present_keys(fields: list[dict]) -> set[str]:
    present = set()
    for f in fields:
        rv = f["review"]
        if rv and rv["outcome"] in ("rejected", "unsure"):
            continue
        if f["value"] is None and not (rv and rv["outcome"] == "corrected"):
            continue
        present.add(f["field"])
    return present


def for_extraction(scenario: str, fields: list[dict]) -> dict:
    gaps = missing(scenario, present_keys(fields))
    return {
        "missing_information": [{"field_name": r.key, "label": r.label, "is_danger_sign": r.danger_sign, "status": "needs_human_review"} for r in gaps],
        "follow_up_questions": question_bank.questions_for(gaps),
    }
