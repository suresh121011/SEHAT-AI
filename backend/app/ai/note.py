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

# The mandated wording (docs/04 §Safety, docs/07) is kept verbatim; `provider_notice` says what produced the values.
DISCLAIMER = "AI-drafted, pending review by a qualified clinician. Not a diagnosis. Research prototype, not a clinically validated device."
FAKE_NOTICE = "Values were extracted by the fake provider, a deterministic keyword extractor (not an LLM), for development and demos."
_TAGS = {"reviewed": "", "document_reviewed": "", "unreviewed_ai_extracted": " [unreviewed]"}
# Alarms first so that length limits can never cut them; then reviewed values; unreviewed last.
_ORDER = {"red_flag": 0}
MAX_SUMMARY_CHARS = 500
_LABELS = {"chief_complaint": "Chief complaint", "onset": "Onset", "duration": "Duration", "temp": "Temperature", "spo2": "SpO2", "pulse": "Pulse",
           "resp_rate": "Respiratory rate", "bp": "BP", "hb": "Hb", "platelets": "Platelets", "creatinine": "Creatinine", "troponin": "Troponin",
           "glucose": "Glucose", "pain_severity": "Pain score (0-10)"}


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_id: uuid.UUID


def _fmt_num(x) -> str:
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


def _ocr_value_text(value) -> str:
    """A reviewed OCR value as printed on the report: comparator, value (or qualitative result), unit, and the
    report's own printed flag (H/L), labelled as printed. No interpretation is added. The printed range is not
    repeated here (a long range such as "1,50,000 - 4,50,000" trips the identifier guard); it stays in the field."""
    if not isinstance(value, dict):
        return str(value)
    shown = value.get("qualitative") or value.get("value") or ""
    text = f"{value.get('comparator') or ''}{shown}".strip()
    if value.get("unit"):
        text = f"{text} {value['unit']}"
    if value.get("flag"):
        text = f"{text} (printed flag: {value['flag']})"
    return text


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
        if v["negated"]:
            return f"Source text denies: {flag} (not a completed red-flag screen)."
        return f"Red-flag mention to check on the screen: {flag}."
    if kind.startswith("ocr_"):
        return f"Document value (reviewed): {v['name']} {_ocr_value_text(v['value'])}."
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
        claims.append({"text": text, "field_ids": [f["field_id"]], "review_state": state, "status": f["status"], "agreement": f["agreement"],
                       "kind": f["kind"], "alarm": f["kind"] == "red_flag" and not v.get("negated")})
    claims.sort(key=lambda c: (not c["alarm"], _ORDER.get(c["kind"], 1), c["review_state"] == "unreviewed_ai_extracted"))
    return claims, blocked, needs_entry


def validate_claims(claims: list[dict], field_ids: set[str]) -> list[dict]:
    """Every claim must cite at least one field of this extraction (also the contract for future model-written claims)."""
    return [c for c in claims if c["field_ids"] and set(c["field_ids"]) <= field_ids]


def summary(claims: list[dict]) -> tuple[str, int]:
    """Claims in order (alarms first), each sentence tagged if unreviewed, cut at a sentence boundary."""
    parts, used = [], 0
    for c in claims:
        text = c["text"][:-1] + _TAGS[c["review_state"]] + "." if c["text"].endswith(".") else c["text"] + _TAGS[c["review_state"]]
        add = len(text) + (1 if parts else 0)
        if used + add > MAX_SUMMARY_CHARS:
            break
        parts.append(text)
        used += add
    return " ".join(parts), len(claims) - len(parts)


def urgency_block(triage_row, urgency_json: dict, extraction_at: str) -> dict:
    """`recorded_urgency` is the rules-engine result of the latest triage run: the only recorded urgency.
    `if_ai_suggestion_accepted` shows what `enforce_raise_only` would give if a human accepted the AI suggestion."""
    suggestion = urgency_json.get("suggestion")
    disputed = [c for c in urgency_json.get("candidates", []) if suggestion is None]
    base = {"ai_suggestion": suggestion, "ai_suggestion_candidates": disputed, "ai_suggestion_evidence": urgency_json.get("evidence", [])}
    if triage_row is None:
        return {"status": "not_determined", "message": "Urgency not determined by the rules engine — run triage on the case; human triage required.",
                "recorded_urgency": None, **base, "if_ai_suggestion_accepted": None, "warnings": []}
    result = TriageResult.model_validate_json(triage_row["result_json"])
    final = enforce_raise_only(result, Urgency(suggestion) if suggestion else None)
    warnings = ["triage_run_predates_extraction: re-run triage after entering reviewed values"] if triage_row["created_at"] < extraction_at else []
    return {
        "status": "rules_engine",
        "triage_run_id": triage_row["run_id"], "triage_run_at": triage_row["created_at"],
        "recorded_urgency": result.urgency.value, "rule_ids": [t.rule_id for t in result.triggered_rules],
        "determination": result.determination, "needs_human_review": result.needs_human_review,
        **base,
        "if_ai_suggestion_accepted": final.model_dump(mode="json"),
        "raise_suggested": final.final_urgency != result.urgency,
        "warnings": warnings,
        "message": "The recorded urgency is the rules engine's. An AI suggestion can only raise it, never lower it, and changes nothing until a human acts on it.",
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
        urgency = urgency_block(triage_row, json.loads(run["urgency_json"]), run["created_at"])
        note_id = str(uuid.uuid4())
        note = {
            "note_id": note_id, "case_id": case_id, "extraction_id": extraction_id, "status": "draft_pending_review",
            "provider": run["provider"], "provider_kind": run["provider_kind"], "provider_is_fake": run["provider"] == "fake",
            "urgency": urgency,
            "summary": text, "summary_omitted_claims": omitted,
            "claims": claims, "blocked_claims": blocked,
            "needs_human_entry": needs_entry,
            "missing_information": gaps["missing_information"],
            "follow_up_questions": gaps["follow_up_questions"],
            "counterfactuals": run_counterfactuals(triage_row),
            "pending_review": [{"field_id": f["field_id"], "field": f["field"], "priority_review": f["priority_review"]} for f in fields if f["needs_review"]],
            "disclaimer": DISCLAIMER, "provider_notice": FAKE_NOTICE if run["provider"] == "fake" else None,
            "clinical_use_allowed": False, "requires_sign_off": True,
            "sign_off": "not implemented in this build (Phase 8); this draft is a snapshot and does not update when fields are reviewed later",
        }
        final = urgency.get("if_ai_suggestion_accepted") or {}
        await conn.execute(
            "INSERT INTO ai_note_drafts (note_id, case_id, extraction_id, triage_run_id, recorded_urgency, urgency_if_accepted, blocked_count, note_json, consent_seq, "
            "actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, (SELECT MAX(seq) FROM consent_events WHERE case_id = ?), ?, ?, ?)",
            (note_id, case_id, extraction_id, urgency.get("triage_run_id"), urgency["recorded_urgency"], final.get("final_urgency"), len(blocked),
             json.dumps(note), case_id, principal.user_id, principal.role.value, datetime.now(timezone.utc).isoformat(timespec="microseconds")),
        )
        await audit.record(conn, principal=principal, action="ai_note_drafted", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.AiNoteDetails(
                               note_id=note_id, extraction_id=extraction_id, recorded_urgency=urgency["recorded_urgency"],
                               urgency_if_suggestion_accepted=final.get("final_urgency"), raise_suggested=bool(urgency.get("raise_suggested")),
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
