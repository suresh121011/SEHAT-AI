"""Source-linked triage note draft (docs/16 §6). P0 is a server-side template, not model prose (council
amendment): every claim is built from a stored field and cites its field id, so every sentence links back to
a quote in its source. The output guard still runs over every claim (reviewer corrections are free text).

Urgency comes from the rules engine first: the latest case triage run. The extraction's urgency suggestion
(present only if every MAKER pass agreed) goes through `app.rules.enforce_raise_only`, so it can raise but
never lower the deterministic level. The note never writes `triage_runs`; a raise is shown as a suggestion
that needs a human to act on it. With no triage run, urgency is "not determined".
"""

import json
import uuid
from datetime import datetime, timezone

import aiosqlite
from pydantic import BaseModel, ConfigDict

from app import audit
from app.ai import followup, guard
from app.ai.extract import case_scenario, gate, load_run, still_effective
from app.ai.review import effective_value
from app.auth import Principal
from app.database import read_transaction, transaction
from app.rules import TriageInput, TriageResult, Urgency, enforce_raise_only
from app.rules.counterfactual import counterfactuals

DISCLAIMER = "AI-drafted, pending review by a qualified clinician. Not a diagnosis. Research prototype, not a clinically validated device."
MAX_SUMMARY_CHARS = 500
_LABELS = {"chief_complaint": "Chief complaint", "onset": "Onset", "duration": "Duration", "temp": "Temperature", "spo2": "SpO2", "pulse": "Pulse",
           "resp_rate": "Respiratory rate", "bp": "BP", "hb": "Hb", "platelets": "Platelets", "creatinine": "Creatinine", "troponin": "Troponin",
           "glucose": "Glucose", "pain_severity": "Pain score (0-10)"}


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_id: uuid.UUID


def _fmt_num(x) -> str:
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


def claim_text(f: dict, v) -> str | None:
    kind, key = f["kind"], f["field"].split("#")[0]
    if kind == "text":
        return f"{_LABELS.get(key, key)}: {v}."
    if kind == "measurement":
        unit = f" {v['unit']}" if v.get("unit") else ""
        val = _fmt_num(v["value"]) + (f"/{_fmt_num(v['value2'])}" if v.get("value2") is not None else "")
        return f"{_LABELS.get(key, key)} {val}{unit} (reported)."
    if kind == "symptom":
        return f"{'Denies' if v['negated'] else 'Reports'} {v['name']}."
    if kind == "medication":
        extra = " ".join(p for p in (v.get("dose"), v.get("frequency")) if p)
        return f"Reported medication: {v['name']}{' ' + extra if extra else ''}."
    if kind == "red_flag":
        flag = key.split(":", 1)[1].replace("_", " ")
        return f"Red-flag item denied: {flag}." if v["negated"] else f"Red-flag mention to check on the screen: {flag}."
    if kind.startswith("ocr_"):
        return f"Document value (reviewed): {v['name']} {v['value']}."
    return None


def build_claims(fields: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(claims, blocked, needs_entry). Claims come only from fields a human has not rejected and that have a
    single value; disputed values are listed for human entry instead."""
    claims, blocked, needs_entry = [], [], []
    for f in fields:
        rv = f["review"]
        if rv and rv["outcome"] == "rejected":
            continue
        v = effective_value(f["kind"], f["value"], rv) if f["origin"] == "model" else f["value"]
        if v is None:
            needs_entry.append({"field_id": f["field_id"], "field": f["field"], "candidates": f["candidates"]})
            continue
        text = claim_text(f, v)
        if text is None:
            continue
        reason = guard.check(text)
        if reason:
            blocked.append({"field_id": f["field_id"], "reason": reason})
            continue
        state = "document_reviewed" if f["origin"] != "model" else ("reviewed" if rv and rv["outcome"] in ("accepted", "corrected") else "unreviewed_ai_extracted")
        claims.append({"text": text, "field_ids": [f["field_id"]], "review_state": state, "status": f["status"], "agreement": f["agreement"]})
    return claims, blocked, needs_entry


def validate_claims(claims: list[dict], field_ids: set[str]) -> list[dict]:
    """Every claim must cite at least one field of this extraction (also the contract for future model-written claims)."""
    return [c for c in claims if c["field_ids"] and set(c["field_ids"]) <= field_ids]


def summary(claims: list[dict]) -> tuple[str, int]:
    parts, used = [], 0
    for c in claims:
        add = len(c["text"]) + (1 if parts else 0)
        if used + add > MAX_SUMMARY_CHARS:
            break
        parts.append(c["text"])
        used += add
    return " ".join(parts), len(claims) - len(parts)


def urgency_block(triage_row, urgency_json: dict) -> dict:
    suggestion = urgency_json.get("suggestion")
    disputed = [c for c in urgency_json.get("candidates", []) if suggestion is None]
    if triage_row is None:
        return {"status": "not_determined", "message": "Urgency not determined by the rules engine — run triage on the case; human triage required.",
                "deterministic_urgency": None, "ai_suggestion": suggestion, "ai_suggestion_candidates": disputed, "final": None}
    result = TriageResult.model_validate_json(triage_row["result_json"])
    final = enforce_raise_only(result, Urgency(suggestion) if suggestion else None)
    return {
        "status": "rules_engine",
        "triage_run_id": triage_row["run_id"], "triage_run_at": triage_row["created_at"],
        "deterministic_urgency": result.urgency.value, "rule_ids": [t.rule_id for t in result.triggered_rules],
        "determination": result.determination, "needs_human_review": result.needs_human_review,
        "ai_suggestion": suggestion, "ai_suggestion_candidates": disputed,
        "final": final.model_dump(mode="json"),
        "raise_requires_human_action": final.final_urgency != result.urgency,
        "message": "Rules engine urgency stands; an AI suggestion may only raise it and never lowers it. A raise is a suggestion for the reviewer, not a recorded triage result.",
    }


def run_counterfactuals(triage_row) -> dict | None:
    """From the stored input of the latest triage run; runs recorded before input storage have none."""
    if triage_row is None:
        return None
    if triage_row["input_json"] is None:
        return {"status": "unavailable", "reason": "triage run recorded before inputs were stored"}
    return {"status": "computed", **counterfactuals(TriageInput.model_validate_json(triage_row["input_json"]))}


async def draft(conn: aiosqlite.Connection, principal: Principal, case_id: str, extraction_id: str, request_id=None) -> dict:
    await gate(conn, principal, case_id, request_id)
    async with transaction(conn):
        await still_effective(conn, case_id)
        run, fields = await load_run(conn, case_id, extraction_id)
        async with conn.execute("SELECT run_id, urgency, result_json, input_json, created_at FROM triage_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
            triage_row = await cur.fetchone()
        gaps = followup.for_extraction(await case_scenario(conn, case_id), fields)
        claims, blocked, needs_entry = build_claims(fields)
        claims = validate_claims(claims, {f["field_id"] for f in fields})
        text, omitted = summary(claims)
        urgency = urgency_block(triage_row, json.loads(run["urgency_json"]))
        note_id = str(uuid.uuid4())
        note = {
            "note_id": note_id, "case_id": case_id, "extraction_id": extraction_id, "status": "draft_pending_review",
            "provider": run["provider"], "provider_kind": run["provider_kind"], "synthetic_provider": run["provider"] == "fake",
            "urgency": urgency,
            "summary": text, "summary_omitted_claims": omitted,
            "claims": claims, "blocked_claims": blocked,
            "needs_human_entry": needs_entry,
            "missing_information": gaps["missing_information"],
            "follow_up_questions": gaps["follow_up_questions"],
            "counterfactuals": run_counterfactuals(triage_row),
            "pending_review": [{"field_id": f["field_id"], "field": f["field"], "priority_review": f["priority_review"]} for f in fields if f["needs_review"]],
            "disclaimer": DISCLAIMER, "clinical_use_allowed": False, "requires_sign_off": True,
        }
        final = urgency.get("final") or {}
        await conn.execute(
            "INSERT INTO ai_note_drafts (note_id, case_id, extraction_id, triage_run_id, deterministic_urgency, final_urgency, blocked_count, note_json, consent_seq, "
            "actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, (SELECT MAX(seq) FROM consent_events WHERE case_id = ?), ?, ?, ?)",
            (note_id, case_id, extraction_id, urgency.get("triage_run_id"), urgency["deterministic_urgency"], final.get("final_urgency"), len(blocked),
             json.dumps(note), case_id, principal.user_id, principal.role.value, datetime.now(timezone.utc).isoformat(timespec="microseconds")),
        )
        await audit.record(conn, principal=principal, action="ai_note_drafted", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.AiNoteDetails(
                               note_id=note_id, extraction_id=extraction_id, deterministic_urgency=urgency["deterministic_urgency"],
                               final_urgency=final.get("final_urgency"), raise_applied=bool(urgency.get("raise_requires_human_action")),
                               downgrade_refused=bool(final.get("override_applied")) and final.get("suggested_urgency") is not None
                               and Urgency(final["suggested_urgency"]).rank < Urgency(final["deterministic_urgency"]).rank,
                               claims=len(claims), blocked_claims=len(blocked)))
    return note


async def list_notes(conn, principal: Principal, case_id: str, request_id=None) -> dict:
    await gate(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await still_effective(conn, case_id)
        async with conn.execute("SELECT note_json FROM ai_note_drafts WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
            notes = [json.loads(r["note_json"]) for r in await cur.fetchall()]
    return {"case_id": case_id, "notes": notes}
