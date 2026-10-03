"""Per-field human review of AI-extracted values (docs/16 §6). Append-only events; field by field only
(no bulk accept: council amendment against review fatigue). The reviewed view never submits triage.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app import audit
from app.ai.extract import field_view, gate, latest_reviews, still_effective
from app.auth import Principal
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.privacy.pii import residual_hit


class CorrectedValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: float | Annotated[str, Field(min_length=1, max_length=200)] | None = None
    value2: float | None = None
    unit: str | None = Field(default=None, max_length=20)
    negated: bool | None = None



class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["accepted", "corrected", "rejected", "unsure"]
    corrected: CorrectedValue | None = None
    supersedes: uuid.UUID | None = None  # the latest review event this decision replaces (optimistic concurrency)


def _check(kind: str, body: ReviewBody, row) -> None:
    if body.outcome == "corrected":
        c = body.corrected
        if c is None:
            raise ApiError(422, "CORRECTION_REQUIRED", "A corrected value is required")
        if kind == "measurement" and not isinstance(c.value, float | int):
            raise ApiError(422, "CORRECTION_INVALID", "A measurement correction needs a numeric value")
        if kind in ("text", "medication") and not isinstance(c.value, str):
            raise ApiError(422, "CORRECTION_INVALID", "This correction needs a text value")
        if kind in ("symptom", "red_flag") and c.negated is None:
            raise ApiError(422, "CORRECTION_INVALID", "This correction needs negated true/false")
        if any(isinstance(x, str) and residual_hit(x) for x in (c.value, c.unit)):
            raise ApiError(422, "PII_DETECTED", "The correction looks like it contains an identifier; enter the clinical value only")
    elif body.corrected is not None:
        raise ApiError(422, "CORRECTION_UNEXPECTED", "Only a 'corrected' decision carries a value")
    if body.outcome == "accepted" and json.loads(row["value_json"]) is None:
        raise ApiError(422, "ACCEPT_REQUIRES_VALUE", "A disputed value has no single value to accept; correct it or reject it")


async def review(conn: aiosqlite.Connection, principal: Principal, case_id: str, field_id: str, body: ReviewBody, request_id=None) -> dict:
    await gate(conn, principal, case_id, request_id)
    async with transaction(conn):
        await still_effective(conn, case_id)
        async with conn.execute("SELECT * FROM ai_fields WHERE field_id = ? AND case_id = ?", (field_id, case_id)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise not_found()
        if row["origin"] != "model":
            raise ApiError(409, "REVIEWED_AT_SOURCE", "This value was reviewed in the document review; change it there")
        _check(row["kind"], body, row)
        latest = (await latest_reviews(conn, case_id)).get(field_id)
        if (latest["event_id"] if latest else None) != (str(body.supersedes) if body.supersedes else None):
            raise ApiError(409, "REVIEW_CONFLICT", "This field was reviewed by someone else; reload")
        event_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO ai_field_review_events (event_id, field_id, case_id, outcome, corrected_json, supersedes, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, field_id, case_id, body.outcome, body.corrected.model_dump_json() if body.corrected else None, str(body.supersedes) if body.supersedes else None,
             principal.user_id, principal.role.value, datetime.now(timezone.utc).isoformat(timespec="microseconds")),
        )
        await audit.record(conn, principal=principal, action="ai_field_reviewed", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.AiFieldReviewDetails(field_id=field_id, outcome=body.outcome))
        new = (await latest_reviews(conn, case_id))[field_id]
    return field_view(row, new)


# Measurement → existing triage-form field (app/rules/models.Vitals). Hints only: a human enters them.
_FORM = {"spo2": "vitals.spo2", "pulse": "vitals.pulse", "resp_rate": "vitals.resp_rate"}


def _hints(f: dict, v: dict) -> list[dict]:
    if f["kind"] != "measurement":
        if f["kind"] == "red_flag" and not v.get("negated"):
            flag = f["field"].split(":", 1)[1]
            return [{"form_field": "red_flags_present", "value": flag, "note": "Candidate for the red-flag screen; tick it only after checking the patient"}]
        return []
    name = f["field"].split("#")[0]
    if name in _FORM:
        return [{"form_field": _FORM[name], "value": v["value"]}]
    if name == "bp" and v.get("value2") is not None:
        return [{"form_field": "vitals.sbp", "value": v["value"]}, {"form_field": "vitals.dbp", "value": v["value2"]}]
    if name == "temp" and (v.get("unit") or "").upper() == "C":
        return [{"form_field": "vitals.temp_c", "value": v["value"]}]
    return []


def effective_value(kind: str, value, rv: dict | None):
    """The value a reviewer accepted, or their correction merged onto the extracted shape."""
    if rv is None or rv["outcome"] != "corrected":
        return value
    c = {k: x for k, x in rv["corrected"].items() if x is not None}
    if kind == "text":
        return c.get("value")
    base = dict(value or {})
    if kind == "measurement":
        base.update({k: c[k] for k in ("value", "value2", "unit") if k in c})
    elif kind == "medication":
        base.update({"name": c["value"]} if "value" in c else {})
    elif kind in ("symptom", "red_flag"):
        base.update({"negated": c["negated"]} if "negated" in c else {})
    return base


async def reviewed(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    """Accepted/corrected values of the latest extraction run, with sources and triage-form hints. View only."""
    await gate(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await still_effective(conn, case_id)
        async with conn.execute("SELECT extraction_id FROM ai_extraction_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
            last = await cur.fetchone()
        rows, reviews = [], {}
        if last is not None:
            reviews = await latest_reviews(conn, case_id)
            async with conn.execute("SELECT * FROM ai_fields WHERE extraction_id = ? ORDER BY ordinal", (last["extraction_id"],)) as cur:
                rows = await cur.fetchall()
    values, unresolved = [], []
    for r in rows:
        f = field_view(r, reviews.get(r["field_id"]))
        rv = f["review"]
        if f["origin"] == "ocr_reviewed":
            values.append({"field_id": f["field_id"], "field": f["field"], "value": f["value"], "basis": "document_review", "evidence": f["evidence"], "form_hints": []})
            continue
        if rv is None or rv["outcome"] == "unsure":
            unresolved.append({"field_id": f["field_id"], "field": f["field"], "status": f["status"], "priority_review": f["priority_review"], "state": "undecided" if rv is None else "unsure"})
            continue
        if rv["outcome"] == "rejected":
            continue
        v = effective_value(f["kind"], f["value"], rv)
        values.append({"field_id": f["field_id"], "field": f["field"], "value": v, "basis": "reviewer_corrected" if rv["outcome"] == "corrected" else "ai_extracted_accepted_by_reviewer",
                       "reviewed_by_role": rv["actor_role"], "evidence": f["evidence"], "form_hints": _hints(f, v) if isinstance(v, dict) else []})
    return {"case_id": case_id, "extraction_id": last["extraction_id"] if last else None, "values": values, "unresolved": unresolved,
            "note": "View only. Nothing here submits triage or changes urgency; enter values on the triage form yourself after checking them."}
