"""FHIR R4 export of a referral (docs/06 §4; architecture §16 "fhir_bundle"). Pure: built from the letter, so the
bundle states exactly what the printed letter states.

A `document` Bundle: Composition (LOINC 57133-1 "Referral note") first, then Patient (the token only: no name,
contact or address), the two Organizations, the PHC Encounter, the referral itself as a ServiceRequest (SNOMED CT
3457005 "Patient referral"), vital-sign Observations (LOINC codes, UCUM units; only values that were recorded) and a
RiskAssessment carrying the rules-engine urgency and its cited sources. Resource ids are UUIDv5 from the referral id,
so a referral always exports with the same resource ids (statuses change as the referral moves on).

Structure is checked by tests (tests/referral_notes/test_referral_fhir.py). It has not been run through an official
FHIR validator or profile (for example the vital-signs profile or ABDM's), so it is "FHIR R4-shaped", not certified.
"""

from __future__ import annotations

import html
import uuid
from typing import Any

_NS = uuid.UUID("5c0d1a6e-7f0b-4e7a-9a52-3f1d2b8c4e11")  # fixed namespace for SEHAT referral exports
_UCUM = "http://unitsofmeasure.org"
_LOINC = "http://loinc.org"
_XHTML = '<div xmlns="http://www.w3.org/1999/xhtml">'

# Rules-engine urgency → ServiceRequest.priority (a project mapping: RED needs care now, YELLOW soon).
PRIORITY = {"RED": "stat", "YELLOW": "urgent", "GREEN": "routine"}
# Status → ServiceRequest.status: an open referral is active; a recorded outcome completes it; a patient who did not
# reach the facility ends it without completion.
REQUEST_STATUS = {"outcome_recorded": "completed", "not_reached": "revoked"}

# (letter vital field, LOINC code, LOINC display, UCUM code). Blood pressure is a panel with two components.
_VITAL_CODES: dict[str, tuple[str, str, str]] = {
    "resp_rate": ("9279-1", "Respiratory rate", "/min"),
    "spo2": ("59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", "%"),
    "pulse": ("8867-4", "Heart rate", "/min"),
    "temp_c": ("8310-5", "Body temperature", "Cel"),
}
_BP_PANEL = ("85354-9", "Blood pressure panel with all children optional")
_BP_PARTS = (("8480-6", "Systolic blood pressure"), ("8462-4", "Diastolic blood pressure"))


def _id(referral_id: str, key: str) -> str:
    return str(uuid.uuid5(_NS, f"{referral_id}/{key}"))


def _url(rid: str) -> str:
    return f"urn:uuid:{rid}"


def _div(*lines: str) -> str:
    return _XHTML + "".join(f"<p>{html.escape(str(line))}</p>" for line in lines) + "</div>"


def _quantity(value: float | int, unit: str) -> dict:
    return {"value": value, "unit": unit, "system": _UCUM, "code": unit}


def _observations(letter: dict, ids: dict[str, str], subject: dict, encounter: dict) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    when = letter["triage"]["recorded_at"]
    base = {"status": "final", "subject": subject, "encounter": encounter, "effectiveDateTime": when,
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs",
                                      "display": "Vital Signs"}]}]}
    by_field = {v["field"]: v for v in letter["vitals"]}
    for field, (code, display, unit) in _VITAL_CODES.items():
        if field in by_field:
            rid = ids[f"obs/{field}"]
            out.append((rid, {"resourceType": "Observation", "id": rid, **base,
                              "code": {"coding": [{"system": _LOINC, "code": code, "display": display}], "text": by_field[field]["label"]},
                              "valueQuantity": _quantity(by_field[field]["value"], unit)}))
    if "blood_pressure" in by_field:
        sbp, dbp = (int(x) for x in str(by_field["blood_pressure"]["value"]).split("/"))
        rid = ids["obs/blood_pressure"]
        out.append((rid, {"resourceType": "Observation", "id": rid, **base,
                          "code": {"coding": [{"system": _LOINC, "code": _BP_PANEL[0], "display": _BP_PANEL[1]}], "text": "Blood pressure"},
                          "component": [{"code": {"coding": [{"system": _LOINC, "code": c, "display": d}]}, "valueQuantity": _quantity(v, "mm[Hg]")}
                                        for (c, d), v in zip(_BP_PARTS, (sbp, dbp))]}))
    return out


def build_bundle(letter: dict[str, Any]) -> dict[str, Any]:
    rid = letter["referral_id"]
    keys = ["composition", "patient", "org/from", "org/to", "encounter", "request", "risk"]
    keys += [f"obs/{f}" for f in (*_VITAL_CODES, "blood_pressure")]
    ids = {k: _id(rid, k) for k in keys}
    patient = {"reference": _url(ids["patient"])}
    encounter = {"reference": _url(ids["encounter"])}
    frm, to = letter["from_facility"], letter["to_facility"]
    from_ref = {"reference": _url(ids["org/from"]), "display": frm["name"]}
    to_ref = {"reference": _url(ids["org/to"]), "display": to["name"]}
    urgency = letter["urgency"]
    transport = letter["transport"]
    transport_line = "Transport: " + transport["label"] + ("; escort required" if transport["escort_required"] else "; no escort required")
    if transport["travel_minutes"]:
        transport_line += f"; about {transport['travel_minutes']} min travel"
    flag_lines = [f"{f['rule_id']}: {f['reason']} ({f['source']})" for f in letter["flags"]]
    vital_lines = [f"{v['label']}: {v['value']}" + (f" {v['unit']}" if v["unit"] else "") for v in letter["vitals"]]
    if letter["missing_fields"]:
        vital_lines.append("Not recorded: " + ", ".join(letter["missing_fields"]))

    observations = _observations(letter, ids, patient, encounter)
    sources = list(dict.fromkeys(f["source"] for f in letter["flags"]))
    raised = urgency["source"] == "reviewer_raise"
    risk_note = ("Protocol rules, not a diagnosis. " + (f"Raised by a reviewer from {urgency['rules_urgency']} (the rules urgency). " if raised else "")
                 + "Reviewed and signed off by a medical officer.")
    org = lambda key, f: {"resourceType": "Organization", "id": ids[key],  # noqa: E731
                          "identifier": [{"system": "urn:sehat:facility", "value": f["code"]}], "name": f["name"]}
    resources: list[tuple[str, dict]] = [
        (ids["patient"], {"resourceType": "Patient", "id": ids["patient"],
                          "identifier": [{"system": "urn:sehat:patient-token", "value": letter["patient_token"]}]}),
        (ids["org/from"], org("org/from", frm)),
        (ids["org/to"], org("org/to", to)),
        (ids["encounter"], {"resourceType": "Encounter", "id": ids["encounter"], "status": "finished",
                            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB", "display": "ambulatory"},
                            "subject": patient, "serviceProvider": from_ref, "period": {"start": letter["triage"]["recorded_at"]}}),
        (ids["request"], {"resourceType": "ServiceRequest", "id": ids["request"],
                          "status": REQUEST_STATUS.get(letter["status"], "active"), "intent": "order",
                          "category": [{"coding": [{"system": "http://snomed.info/sct", "code": "3457005", "display": "Patient referral"}]}],
                          "priority": PRIORITY[urgency["level"]], "subject": patient, "encounter": encounter,
                          "authoredOn": letter["created_at"], "requester": from_ref, "performer": [to_ref],
                          "reasonCode": [{"text": letter["reason"]["label"]}],
                          "note": [{"text": transport_line}, {"text": "Insurance: " + letter["insurance"]["label"]}],
                          "text": {"status": "generated", "div": _div("Referral to " + to["name"], "Reason: " + letter["reason"]["label"], transport_line)}}),
        *observations,
        (ids["risk"], {"resourceType": "RiskAssessment", "id": ids["risk"], "status": "final", "subject": patient, "encounter": encounter,
                       "occurrenceDateTime": letter["triage"]["recorded_at"],
                       "method": {"text": f"SEHAT AI deterministic rules engine {letter['triage']['engine_version']}, ruleset {letter['triage']['ruleset_version']}"},
                       "prediction": [{"qualitativeRisk": {"coding": [{"system": "urn:sehat:triage-urgency", "code": urgency["level"],
                                                                      "display": f"{urgency['level']}: {urgency['meaning']}"}]}}],
                       **({"basis": [{"display": s} for s in sources]} if sources else {}),
                       "note": [{"text": risk_note}]}),
    ]
    sections = [
        {"title": "Reason for referral", "text": {"status": "generated", "div": _div(letter["reason"]["label"])},
         "entry": [{"reference": _url(ids["request"])}]},
        {"title": "Triage urgency",
         "text": {"status": "generated", "div": _div(f"{urgency['level']}: {urgency['meaning']}"
                                                     + (f" (raised by a reviewer from {urgency['rules_urgency']})" if raised else " (protocol rules)"),
                                                     *flag_lines)},
         "entry": [{"reference": _url(ids["risk"])}]},
        {"title": "Vital signs", "text": {"status": "generated", "div": _div(*(vital_lines or ["None recorded"]))},
         **({"entry": [{"reference": _url(r)} for r, _ in observations]} if observations else
            {"emptyReason": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/list-empty-reason", "code": "unavailable"}]}})},
        {"title": "Transport plan", "text": {"status": "generated", "div": _div(transport_line)}},
    ]
    composition = {"resourceType": "Composition", "id": ids["composition"], "status": "final",
                   "type": {"coding": [{"system": _LOINC, "code": "57133-1", "display": "Referral note"}]},
                   "subject": patient, "encounter": encounter, "date": letter["created_at"], "author": [from_ref],
                   "title": "SEHAT AI referral note",
                   "attester": [{"mode": "professional", "time": letter["sign_off"]["signed_off_at"],
                                 "party": {"display": "Medical officer (signed off in SEHAT AI)"}}],
                   "section": sections}
    entries = [(ids["composition"], composition), *resources]
    return {"resourceType": "Bundle", "id": _id(rid, "bundle"),
            "identifier": {"system": "urn:sehat:referral", "value": rid},
            "type": "document", "timestamp": letter["created_at"],
            "entry": [{"fullUrl": _url(r), "resource": res} for r, res in entries]}
