"""P0-2 "Check these first" (docs/implementation/p0-2-verification.md): the unchecked evidence of one case, in the order a
person should check it. Read-only and deterministic: it never confirms, changes or records a value, and never writes
triage runs or review events.

Order (each item says why it is where it is):
1. `match_document`: a document nobody has matched to this patient and visit. Its rows cannot be confirmed until then.
2. `could_change_urgency`: a spoken vital not yet read back whose value, if it were entered on the triage form, would
   change the rules engine's urgency. Found by re-running the engine on the latest triage run's stored input with that
   one value substituted, as the counterfactual explainer does (app.rules.counterfactual). Hypothetical, display only.
3. `abnormal_lab`: a machine-read lab row with a printed H/L flag or a value outside its printed or sourced range. The
   rules engine never reads lab values, so this key keeps labs from dropping to the bottom.
4. `routine`: everything else, in document and recording order.
"""

import json

import aiosqlite
from pydantic import ValidationError

from app import consent
from app.auth import Principal
from app.database import read_transaction, transaction
from app.errors import ApiError
from app.rules import TriageInput, evaluate_triage

PRIORITIES = ("match_document", "could_change_urgency", "abnormal_lab", "routine")
NOTE = ("An order for checking only. Nothing here is confirmed, changed or recorded, and it never changes urgency: the "
        "'if confirmed' urgency is hypothetical, from the rules engine, and only a value a person enters on the triage form counts.")
_OPEN = ("machine_read", "unsure")
_VOICE_LABEL = {"temp": "Temperature", "spo2": "SpO2", "pulse": "Pulse", "resp_rate": "Breathing rate", "bp": "Blood pressure",
                "age": "Age", "pregnancy": "Pregnancy", "unassigned": "Number heard (not linked to a vital)"}
_VITAL_KEYS = ("temp_c", "spo2", "pulse", "resp_rate", "sbp", "dbp")


def _evaluate(data: dict):
    try:
        return evaluate_triage(TriageInput.model_validate(data)).urgency.value
    except (ValidationError, ValueError):
        return None


def urgency_if_confirmed(base: TriageInput, normalized: dict) -> dict | None:
    """The rules urgency now, and if this one spoken value replaced the triage-form value. None when the engine cannot
    run on the substituted input (the value is outside the engine's own input range)."""
    data = base.model_dump(mode="python")
    for k, v in normalized.items():
        if k in _VITAL_KEYS:
            data["vitals"][k] = v
        elif k in ("age_years", "pregnant"):
            data[k] = v
    now, then = _evaluate(base.model_dump(mode="python")), _evaluate(data)
    return None if now is None or then is None else {"now": now, "if_confirmed": then, "hypothetical": True}


def _lab_reasons(f: dict) -> tuple[list[str], bool]:
    reasons, abnormal = [], False
    flag = f.get("printed_flag")
    if flag in ("H", "L", "abnormal"):
        reasons.append({"H": "printed_flag_high", "L": "printed_flag_low", "abnormal": "printed_flag_abnormal"}[flag])
        abnormal = True
    if f.get("printed_range_status") in ("below_range", "above_range"):
        reasons.append("outside_printed_range")
        abnormal = True
    if (f.get("reference") or {}).get("status") in ("below_range", "above_range"):
        reasons.append("outside_reference_range")
        abnormal = True
    return reasons, abnormal


def _field_shown(f: dict) -> str:
    if f["kind"] == "lab":
        unit = (f.get("unit") or {}).get("raw") or ""
        return f"{(f.get('value') or {}).get('raw') or '—'} {unit}".strip()
    return " ".join(p for p in (f.get("drug_raw"), f.get("strength_raw"), f.get("dosage_pattern"), f.get("frequency_raw"), f.get("duration_raw")) if p)


def rank(docs: list[dict], voice: list[dict], base: TriageInput | None) -> list[dict]:
    """Pure. `docs`: document views (app.ocr.service.document_view). `voice`: unresolved spoken values, each
    {candidate_id, field, normalized, display, state, can_confirm}. `base`: the latest triage run's stored input or None."""
    items: list[dict] = []
    for d in docs:
        if d.get("status") != "completed":
            continue
        att = d.get("attestation")
        matched = bool(att) and att.get("answer") == "matches"
        if not matched:
            items.append({"item_id": f"document:{d['document_id']}", "kind": "document_attestation", "priority": "match_document",
                          "reasons": ["not_answered" if not att else f"answered_{att['answer']}"], "label": d.get("document_type", "document"),
                          "shown": None, "ref": {"document_id": d["document_id"]}, "act_on": "documents", "can_confirm": None, "urgency_if_confirmed": None})
        for f in d.get("fields", []):
            if f.get("review_status") not in _OPEN:
                continue
            reasons, abnormal = _lab_reasons(f) if f["kind"] == "lab" else ([], False)
            if f.get("review_status") == "unsure":
                reasons.append("marked_not_sure")
            if f.get("disputed"):
                reasons.append("readings_differ")
            if f.get("band") == "human_entry":
                reasons.append("could_not_be_read")
            if not f.get("can_confirm"):
                reasons.append("needs_correction")
            if not matched:
                reasons.append("document_not_matched_to_patient")
            items.append({"item_id": f"document_field:{f['field_id']}", "kind": "document_field", "priority": "abnormal_lab" if abnormal else "routine",
                          "reasons": reasons, "label": (f.get("name_raw") if f["kind"] == "lab" else f.get("drug_raw")) or "(no name read)",
                          "shown": _field_shown(f), "ref": {"document_id": d["document_id"], "field_id": f["field_id"]}, "act_on": "documents",
                          "can_confirm": bool(f.get("can_confirm")) and matched, "urgency_if_confirmed": None})
    for v in voice:
        reasons, change = [], None
        if v["state"] == "unsure":
            reasons.append("marked_not_sure")
        if v["normalized"] is None or not v["can_confirm"]:
            reasons.append("needs_correction")
        elif base is None:
            reasons.append("no_triage_run_to_compare")
        else:
            change = urgency_if_confirmed(base, v["normalized"])
            if change is None:
                reasons.append("could_not_compare")
            elif change["now"] != change["if_confirmed"]:
                reasons.append("would_change_rules_urgency")
        items.append({"item_id": f"voice:{v['candidate_id']}", "kind": "voice_value", "field": v["field"],
                      "priority": "could_change_urgency" if "would_change_rules_urgency" in reasons else "routine",
                      "reasons": reasons, "label": _VOICE_LABEL.get(v["field"], v["field"]), "shown": v["display"],
                      "ref": {"candidate_id": v["candidate_id"]}, "act_on": "voice", "can_confirm": v["can_confirm"], "urgency_if_confirmed": change})
    return sorted(items, key=lambda i: PRIORITIES.index(i["priority"]))  # stable: document and recording order within a priority


async def _gate(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id) -> None:
    """Same access as the encounter summary: the creator ANM or a medical officer, with `triage` consent in effect."""
    denial = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def _voice_items(conn: aiosqlite.Connection, case_id: str) -> list[dict]:
    from app.voice import readback
    from app.voice.service import case_resolutions

    _, unresolved, _ = await case_resolutions(conn, case_id)
    out = []
    for u in unresolved:
        async with conn.execute("SELECT c.*, t.language FROM voice_candidates c JOIN voice_transcriptions t USING (transcription_id) WHERE c.candidate_id = ?",
                                (u["candidate_id"],)) as cur:
            c = await cur.fetchone()
        normalized = json.loads(c["normalized_json"]) if c["normalized_json"] else None
        flags = json.loads(c["flags_json"])
        out.append({"candidate_id": u["candidate_id"], "field": u["field"], "normalized": normalized, "state": u["state"],
                    "display": readback.display_value(c["field"], c["raw_value"], c["raw_value2"], c["unit"], normalized),
                    "can_confirm": readback.can_confirm(c["field"], normalized, flags)})
    return out


async def queue(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    from app.ocr.service import document_view

    await _gate(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        if not (await consent.snapshot(conn, case_id)).is_effective("triage"):
            raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed; reload")
        async with conn.execute("SELECT document_id FROM ocr_documents WHERE case_id = ? ORDER BY created_at", (case_id,)) as cur:
            docs = [await document_view(conn, r["document_id"]) for r in await cur.fetchall()]
        voice = await _voice_items(conn, case_id)
        async with conn.execute("SELECT input_json FROM triage_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
            run = await cur.fetchone()
    base = TriageInput.model_validate_json(run["input_json"]) if run and run["input_json"] else None
    items = rank(docs, voice, base)
    counts = {p: sum(i["priority"] == p for i in items) for p in PRIORITIES}
    return {"case_id": case_id, "items": items, "counts": {**counts, "total": len(items)}, "note": NOTE, "clinical_use_allowed": False}
