"""Counterfactual explanations (docs/09 §6.7, architecture §20). Pure: no I/O, no LLM.

"If X were Y, urgency would be Z" — found by re-running the deterministic engine, never by new thresholds:
for each recorded vital, the nearest value (searching outward in steps across the engine's own input
range) at which the engine's urgency changes; for each red flag entered, its removal; and the red-flag
screen left incomplete. The engine is the only judge of every answer, so these explain the rules as
implemented; they are not clinical advice and not a statement about the patient.
"""

from pydantic import ValidationError

from app.rules.engine import evaluate_triage
from app.rules.models import TriageInput

# (field, step, low, high) — ranges are the existing Vitals bounds (app/rules/models.py).
_PROBES = (("spo2", 1, 0, 100), ("resp_rate", 1, 0, 80), ("pulse", 1, 0, 300), ("sbp", 1, 0, 300), ("temp_c", 0.1, 25.0, 45.0))
MAX_STEPS = 400


def _rerun(data: dict):
    try:
        return evaluate_triage(TriageInput.model_validate(data))
    except (ValidationError, ValueError):
        return None


def _vital_change(base: dict, field: str, step: float, lo: float, hi: float, urgency) -> dict | None:
    cur = base["vitals"].get(field)
    if cur is None:
        return None
    best = None
    for direction in (-1, 1):
        for i in range(1, MAX_STEPS + 1):
            v = round(cur + direction * i * step, 1) if isinstance(step, float) else cur + direction * i
            if v < lo or v > hi:
                break
            res = _rerun({**base, "vitals": {**base["vitals"], field: v}})
            if res is not None and res.urgency != urgency:
                if best is None or i < best[0]:
                    best = (i, v, res)
                break
    if best is None:
        return None
    _, v, res = best
    return {"if_changed": {"field": f"vitals.{field}", "from": cur, "to": v}, "then_urgency": res.urgency.value,
            "rule_ids": [t.rule_id for t in res.triggered_rules], "distance_steps": best[0]}


def counterfactuals(data: TriageInput, limit: int = 3) -> dict:
    base_result = evaluate_triage(data)
    base = data.model_dump(mode="json")
    out: list[dict] = []
    for flag in base.get("red_flags_present", []):
        res = _rerun({**base, "red_flags_present": [f for f in base["red_flags_present"] if f != flag]})
        if res is not None and res.urgency != base_result.urgency:
            out.append({"if_changed": {"field": "red_flags_present", "remove": flag}, "then_urgency": res.urgency.value,
                        "rule_ids": [t.rule_id for t in res.triggered_rules], "distance_steps": 0})
    if base.get("red_flag_screen_completed"):
        res = _rerun({**base, "red_flag_screen_completed": False, "red_flags_present": []})
        if res is not None and res.urgency != base_result.urgency:
            out.append({"if_changed": {"field": "red_flag_screen_completed", "to": False}, "then_urgency": res.urgency.value,
                        "rule_ids": [t.rule_id for t in res.triggered_rules], "distance_steps": 0})
    vitals = [c for p in _PROBES if (c := _vital_change(base, *p, base_result.urgency)) is not None]
    out += sorted(vitals, key=lambda c: c["distance_steps"])
    return {
        "urgency": base_result.urgency.value,
        "counterfactuals": out[:limit],
        "note": "Computed by re-running the deterministic rules engine with one input changed. Explains the rules as implemented; not clinical advice.",
    }
