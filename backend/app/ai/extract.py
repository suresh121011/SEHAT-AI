"""Extraction runs (docs/16 §4–§5, §9): inputs → privacy gateway (consent, per-segment redaction) → MAKER
passes (validate, ground, vote) → append-only storage inside the gateway's consent-checked T2 transaction.

Nothing here reads or writes `triage_runs`, and nothing submits triage. Every stored field needs a
human decision (`needs_review`); reviewed OCR values are carried over as already human-reviewed.
"""

import json
import re
import uuid
from datetime import datetime, timezone

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app import audit, consent
from app.ai import guard, inputs, maker
from app.ai.adapter import StructuredProvider
from app.ai.prompts import PROMPT_VERSION
from app.ai.schemas import SCHEMA_VERSION
from app.auth import Principal
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.privacy import gateway
from app.privacy.pii import RedactedPrompt, RedactedSegment

NOTE = "AI-extracted candidates. Every value needs a human decision; nothing here changes urgency or submits triage."


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    intake_text: str | None = Field(default=None, max_length=inputs.MAX_INTAKE_CHARS)
    include_voice: bool = True
    include_ocr_reviewed: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ── Access ───────────────────────────────────────────────────────────────────────────────────────


async def gate(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id: str | None) -> None:
    """Reviewer access (ANM creator or medical officer) and effective `triage` + `ai_assist` consent. Stored
    AI output is not served after either is withdrawn (rows are kept; deletion is deferred, docs/16 §8)."""
    denial = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
            await consent.require(conn, case_id, "ai_assist")
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def still_effective(conn: aiosqlite.Connection, case_id: str) -> None:
    snap = await consent.snapshot(conn, case_id)
    if not (snap.is_effective("triage") and snap.is_effective("ai_assist")):
        raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed; reload")


# ── Source references ────────────────────────────────────────────────────────────────────────────


def locate(text: str, quote: str) -> tuple[int, int] | None:
    words = quote.split()
    if not words:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), text, re.IGNORECASE)
    return (m.start(), m.end()) if m else None


def source_ref(seg: RedactedSegment, source: dict, quote: str) -> dict:
    loc = locate(seg.text, quote)
    ref = {"segment_id": seg.segment_id, "type": source["type"], "redacted_chars": list(loc) if loc else None}
    if source["type"] == "transcript":
        ref["transcription_id"] = source["transcription_id"]
        base = source["transcript_chars"][0]
        io = seg.input_offsets(*loc) if loc else None
        ref["transcript_chars"] = [base + io[0], base + io[1]] if io else None
        ref["segment_transcript_chars"] = source["transcript_chars"]
    elif source["type"] == "transcript_translated":
        ref["transcription_id"] = source["transcription_id"]
        ref["original_chars"] = source["original_chars"]  # sentence level: the quote is in the translation
        ref["machine_translated_unreviewed"] = True
    return ref


# ── Create ───────────────────────────────────────────────────────────────────────────────────────


async def _existing(conn, case_id: str, key: str) -> str | None:
    async with conn.execute("SELECT extraction_id FROM ai_extraction_runs WHERE case_id = ? AND idempotency_key = ?", (case_id, key)) as cur:
        row = await cur.fetchone()
    return row["extraction_id"] if row else None


async def create(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: ExtractionRequest, provider: StructuredProvider,
                 passes: int, request_id: str | None, translator=None) -> dict:
    key = str(body.idempotency_key)
    await gate(conn, principal, case_id, request_id)
    if (eid := await _existing(conn, case_id, key)) is not None:  # retry: never re-runs the provider
        return await view(conn, principal, case_id, eid, request_id)

    src = await inputs.build(conn, case_id, body.intake_text, body.include_voice, translator)
    ocr_values: list[dict] = []
    if body.include_ocr_reviewed:
        from app.ocr import service as ocr_service

        ocr_values = (await ocr_service.reviewed(conn, principal, case_id, request_id))["values"]
    if not src.segments and not ocr_values:
        raise ApiError(422, "AI_NO_INPUT", "Nothing to extract: add intake text or a completed English transcript", {"skipped_sources": src.skipped})
    sources = {s.segment_id: s.source for s in src.segments}
    instruction_like = any(guard.looks_like_instructions(s.raw) for s in src.segments)

    async def run(prompt: RedactedPrompt):
        if not prompt.segments:
            return maker.VoteResult("completed", 0, 0)
        return await maker.run_passes(provider, prompt, passes)

    async def persist(c, prompt: RedactedPrompt, res: maker.VoteResult, authz_seq: int) -> str:
        if (existing := await _existing(c, case_id, key)) is not None:
            return existing
        eid, now = str(uuid.uuid4()), _now()
        segs = {s.segment_id: s for s in prompt.segments}
        flags = ["possible_instruction_text"] if instruction_like else []
        await c.execute(
            "INSERT INTO ai_extraction_runs (extraction_id, case_id, created_by, actor_role, idempotency_key, provider, provider_kind, model_id, prompt_version, "
            "schema_version, passes_requested, passes_valid, status, consent_seq, segments_json, skipped_json, dropped_json, abstentions_json, urgency_json, flags_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, case_id, principal.user_id, principal.role.value, key, provider.name, provider.kind, provider.model_id, PROMPT_VERSION, SCHEMA_VERSION,
             res.passes_requested, res.passes_valid, res.status, authz_seq,
             json.dumps([{"segment_id": s.segment_id, "text": s.text, "redacted_total": s.redacted_total, "source": sources[s.segment_id]} for s in prompt.segments]),
             json.dumps(src.skipped), json.dumps(res.dropped), json.dumps(res.abstentions),
             json.dumps({"suggestion": res.urgency_suggestion, "candidates": res.urgency_candidates}), json.dumps(flags), now),
        )
        ordinal = 0
        for f in res.fields:
            evidence = [{**e, "source": source_ref(segs[e["segment_id"]], sources[e["segment_id"]], e["quote"])} for e in f.evidence]
            flags_f = list(f.flags) + (["machine_translated_unreviewed"] if any(sources[e["segment_id"]]["type"] == "transcript_translated" for e in f.evidence) else [])
            await c.execute(
                "INSERT INTO ai_fields (field_id, extraction_id, case_id, ordinal, origin, field_key, kind, status, agreement, value_json, candidates_json, evidence_json, "
                "critical, priority_review, flags_json, created_at) VALUES (?, ?, ?, ?, 'model', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), eid, case_id, ordinal, f.key, f.kind, f.status, f.agreement, json.dumps(f.value), json.dumps(f.candidates),
                 json.dumps(evidence), int(f.critical), int(f.priority_review), json.dumps(flags_f), now),
            )
            ordinal += 1
        for v in ocr_values:
            await c.execute(
                "INSERT INTO ai_fields (field_id, extraction_id, case_id, ordinal, origin, field_key, kind, status, agreement, value_json, candidates_json, evidence_json, "
                "critical, priority_review, flags_json, created_at) VALUES (?, ?, ?, ?, 'ocr_reviewed', ?, ?, 'human_reviewed', NULL, ?, '[]', ?, 0, 0, '[]', ?)",
                (str(uuid.uuid4()), eid, case_id, ordinal, f"ocr:{v['name']}", f"ocr_{v['kind']}", json.dumps({"name": v["name"], "value": v["value"], "outcome": v["outcome"]}),
                 json.dumps([{"source": v["source"]}]), now),
            )
            ordinal += 1
        await audit.record(c, principal=principal, action="ai_extraction_recorded", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.AiExtractionDetails(
                               extraction_id=eid, provider=provider.name, status=res.status, passes_requested=res.passes_requested, passes_valid=res.passes_valid,
                               segments=len(prompt.segments), skipped_sources=len(src.skipped), fields=len(res.fields),
                               disputed=sum(f.status == "disputed" for f in res.fields), disputed_raise=sum(f.status == "disputed_raise" for f in res.fields),
                               dropped_ungrounded=len(res.dropped), urgency_suggestion=res.urgency_suggestion))
        return eid

    raw_segments = [(s.segment_id, s.raw) for s in src.segments]
    del src.segments
    eid = await gateway.submit_structured(conn, principal, case_id, raw_segments, run, persist, request_id)
    return await view(conn, principal, case_id, eid, request_id, gated=True)


# ── Read ─────────────────────────────────────────────────────────────────────────────────────────


async def latest_reviews(conn, case_id: str) -> dict[str, aiosqlite.Row]:
    async with conn.execute(
        "SELECT r.* FROM ai_field_review_events r JOIN (SELECT field_id, MAX(seq) AS s FROM ai_field_review_events WHERE case_id = ? GROUP BY field_id) m "
        "ON r.seq = m.s", (case_id,),
    ) as cur:
        return {r["field_id"]: r for r in await cur.fetchall()}


def field_view(r, review) -> dict:
    v = {
        "field_id": r["field_id"], "origin": r["origin"], "field": r["field_key"], "kind": r["kind"], "status": r["status"], "agreement": r["agreement"],
        "value": json.loads(r["value_json"]), "candidates": json.loads(r["candidates_json"]), "evidence": json.loads(r["evidence_json"]),
        "critical": bool(r["critical"]), "priority_review": bool(r["priority_review"]), "flags": json.loads(r["flags_json"]),
        "needs_review": r["origin"] == "model",
        "review": None,
    }
    if review is not None:
        v["review"] = {"event_id": review["event_id"], "outcome": review["outcome"], "corrected": json.loads(review["corrected_json"]) if review["corrected_json"] else None,
                       "actor_role": review["actor_role"], "created_at": review["created_at"]}
        v["needs_review"] = review["outcome"] == "unsure"
    return v


async def load_run(conn, case_id: str, extraction_id: str) -> tuple[aiosqlite.Row, list[dict]]:
    async with conn.execute("SELECT * FROM ai_extraction_runs WHERE extraction_id = ? AND case_id = ?", (extraction_id, case_id)) as cur:
        run = await cur.fetchone()
    if run is None:
        raise not_found()
    reviews = await latest_reviews(conn, case_id)
    async with conn.execute("SELECT * FROM ai_fields WHERE extraction_id = ? ORDER BY ordinal", (extraction_id,)) as cur:
        fields = [field_view(r, reviews.get(r["field_id"])) for r in await cur.fetchall()]
    return run, fields


def run_view(run, fields: list[dict]) -> dict:
    return {
        "extraction_id": run["extraction_id"], "case_id": run["case_id"], "status": run["status"], "created_at": run["created_at"],
        "provider": run["provider"], "provider_kind": run["provider_kind"], "model_id": run["model_id"], "synthetic_provider": run["provider"] == "fake",
        "prompt_version": run["prompt_version"], "schema_version": run["schema_version"],
        "maker": {"passes_requested": run["passes_requested"], "passes_valid": run["passes_valid"], "abstentions": json.loads(run["abstentions_json"])},
        "segments": json.loads(run["segments_json"]), "skipped_sources": json.loads(run["skipped_json"]), "dropped": json.loads(run["dropped_json"]),
        "urgency_suggestion": json.loads(run["urgency_json"]), "flags": json.loads(run["flags_json"]),
        "fields": fields,
        "pending_review": [f["field_id"] for f in fields if f["needs_review"]],
        "note": NOTE,
    }


async def view(conn, principal: Principal, case_id: str, extraction_id: str, request_id=None, gated: bool = False) -> dict:
    if not gated:
        await gate(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await still_effective(conn, case_id)
        run, fields = await load_run(conn, case_id, extraction_id)
    return run_view(run, fields)


async def list_runs(conn, principal: Principal, case_id: str, request_id=None) -> dict:
    await gate(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await still_effective(conn, case_id)
        async with conn.execute("SELECT extraction_id, status, provider, created_at FROM ai_extraction_runs WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"case_id": case_id, "extractions": rows}
