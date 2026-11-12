"""The referral letter (docs/21; docs/05 §5.1 "Included evidence"). Pure: built only from the stored referral, its
status events and the signed-off triage run the referral names. No model call and no free text: every sentence is a
closed-list label, a rules-engine reason with its cited source, or a recorded number.

The triage run and its sign-off are append-only, so a letter never changes after the referral is made: a later
re-triage starts a new review and, if needed, a new referral.
"""

from __future__ import annotations

from typing import Any

from app.referral_notes import options as o

TITLE = "Referral note"
DISCLAIMER = ("Prepared with SEHAT AI, a research prototype, not a clinically validated device. The urgency comes from "
              "deterministic protocol rules and is not a diagnosis. A medical officer reviewed and signed off the triage "
              "before this referral. The patient is identified by a token only.")
# Same words as the review screens (frontend/src/components/review/Badges.tsx URGENCY_MEANING).
URGENCY_MEANING = {"RED": "immediate attention", "YELLOW": "urgent review",
                   "GREEN": "routine queue — not a statement that the patient is well"}
SCENARIOS = {"opd": "OPD", "maternal": "Maternal", "chronic_ncd": "Chronic NCD", "health_camp": "Health camp",
             "campus_fever": "Campus fever", "occupational": "Occupational", "referral": "Referral"}

# (field, label, unit). Blood pressure is shown as one line when both values were recorded.
_VITALS: tuple[tuple[str, str, str | None], ...] = (
    ("resp_rate", "Respiratory rate", "breaths/min"),
    ("spo2", "SpO2", "%"),
    ("on_supplemental_oxygen", "On supplemental oxygen", None),
    ("pulse", "Pulse", "beats/min"),
    ("blood_pressure", "Blood pressure", "mmHg"),
    ("temp_c", "Temperature", "°C"),
    ("consciousness", "Consciousness (ACVPU)", None),
    ("gcs", "Glasgow Coma Scale", "/15"),
)


def vitals(recorded: dict[str, Any]) -> list[dict]:
    """Recorded values only. A missing value is left out, never shown as normal (the letter lists missing fields)."""
    out: list[dict] = []
    for field, label, unit in _VITALS:
        if field == "blood_pressure":
            sbp, dbp = recorded.get("sbp"), recorded.get("dbp")
            if sbp is not None and dbp is not None:
                out.append({"field": field, "label": label, "value": f"{sbp}/{dbp}", "unit": unit})
            else:
                for f, lab, v in (("sbp", "Systolic blood pressure", sbp), ("dbp", "Diastolic blood pressure", dbp)):
                    if v is not None:
                        out.append({"field": f, "label": lab, "value": v, "unit": unit})
            continue
        value = recorded.get(field)
        if value is None:
            continue
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        out.append({"field": field, "label": label, "value": value, "unit": unit})
    return out


def flags(result: dict[str, Any]) -> list[dict]:
    """Every rule the engine triggered, in engine order, with its cited source and the recorded evidence."""
    return [{
        "rule_id": t["rule_id"],
        "urgency": t["urgency"],
        "reason": t["reason"],
        "source": t["source"],
        "evidence": [{"field": name, "value": item["value"], "threshold": item["threshold"]} for name, item in t["evidence"].items()],
    } for t in result.get("triggered_rules", [])]


def history(events: list[dict]) -> list[dict]:
    return [{
        "status": e["status"],
        "label": o.STATUS_LABELS[e["status"]],
        "detail_code": e["detail_code"],
        "detail_label": o.detail_label(e["status"], e["detail_code"]),
        "at": e["created_at"],
        "actor_role": e["actor_role"],
    } for e in events]


def build(packet: dict[str, Any], events: list[dict], run: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """`packet`: the referral row plus `patient_token` and `scenario`; `run`: the referenced triage run with parsed
    `result` and `input`; `review`: the review state of that run when the referral was read (sign-off and overrides)."""
    result, recorded = run["result"], run["input"] or {}
    level = packet["urgency"]
    effective = review["effective_urgency"]
    return {
        "title": TITLE,
        "referral_id": packet["referral_id"],
        "case_id": packet["case_id"],
        "patient_token": packet["patient_token"],
        "created_at": packet["created_at"],
        "from_facility": o.facility_view(packet["from_facility"]),
        "to_facility": o.facility_view(packet["to_facility"]),
        "urgency": {
            "level": level,
            "meaning": URGENCY_MEANING[level],
            "rules_urgency": run["urgency"],
            "source": "rules_engine" if level == run["urgency"] else "reviewer_raise",
            "reviewer_recorded": effective if effective != level else None,
        },
        "reason": {"code": packet["reason_code"], "label": o.REASONS[packet["reason_code"]]},
        "scenario": {"code": packet["scenario"], "label": SCENARIOS.get(packet["scenario"], packet["scenario"])},
        "patient": {"age_years": recorded.get("age_years"), "pregnant": bool(recorded.get("pregnant")),
                    "trimester": recorded.get("trimester")},
        "determination": result.get("determination"),
        "missing_fields": list(result.get("missing_fields", [])),
        "flags": flags(result),
        "vitals": vitals(recorded.get("vitals") or {}),
        "transport": {"mode": packet["transport_mode"], "label": o.TRANSPORT[packet["transport_mode"]],
                      "escort_required": bool(packet["escort_required"]), "travel_minutes": packet["travel_minutes"]},
        "insurance": {"scheme": packet["insurance_scheme"], "label": o.INSURANCE[packet["insurance_scheme"]]},
        "sign_off": {"reviewer_role": "medical_officer", "signed_off_at": review["signed_off_at"],
                     "statement": "Triage reviewed and signed off by a medical officer"},
        "triage": {"run_id": run["run_id"], "evaluated_at": result.get("evaluated_at"), "recorded_at": run["created_at"],
                   "engine_version": result.get("engine_version"), "ruleset_version": result.get("ruleset_version")},
        "status": events[-1]["status"],
        "status_history": history(events),
        "disclaimer": DISCLAIMER,
    }
