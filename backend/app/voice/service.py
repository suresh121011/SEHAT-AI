"""Voice transcription, read-back and prefill services (docs/12). Same T1/T2 shape as
app/privacy/gateway.py:

    T1 [txn] case access → consent (triage; + voice_cloud for the cloud engine) → idempotency:
             insert a `pending` row bound to (case, key, user, audio hash) BEFORE any engine runs
       VAD and the engine run outside any transaction / DB lock, in a worker thread
    T2 [txn] consent unchanged since T1? → finalise row + candidates + audit      else discard, 409

Guarantees and limits:
- Audio is held in memory for the request only; it is never written to the database or disk by this
  code, and never logged. (The OS, Python allocator or a crash dump are outside this guarantee.)
- Consent for the cloud engine is checked before any audio leaves the machine. The T2 re-check can only
  discard the *result*: audio already sent to the provider cannot be recalled.
- A retry with the same idempotency key never re-runs an engine or re-sends audio: it returns the stored
  row, or 409 if the key is reused with different audio/user or the first request is still running.
- Transcripts are untrusted machine output, stored as text and never interpreted as instructions or
  markup. They never reach an LLM here; any future LLM use must go through app.privacy.gateway.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
import anyio
from pydantic import BaseModel, ConfigDict, Field

from app import audit, consent
from app.auth import Principal
from app.config import Settings
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.voice import engines, extract, readback
from app.voice.audio import AudioClip

Language = Literal["en", "hi", "or"]
EngineName = Literal["local", "cloud"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# A `pending` row older than this is treated as abandoned (process crash/restart, lost worker). It is
# well above the longest legitimate processing time (cloud timeout 20 s; local inference < 1 s warm).
PENDING_TTL_S = 180


def _age_s(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()


async def _finalize_failed(conn, principal, case_id, transcription_id, engine, reason, request_id, outcome="failure") -> None:
    """Move a still-pending row to `failed` (+ audit). Only touches rows that are still pending."""
    cur = await conn.execute(
        "UPDATE voice_transcriptions SET status = 'failed', failure_code = ?, completed_at = ? WHERE transcription_id = ? AND status = 'pending'",
        (reason, _now(), transcription_id),
    )
    if cur.rowcount:
        await audit.record(conn, principal=principal, action="voice_transcription_failed", outcome=outcome, case_id=case_id, request_id=request_id, details=audit.VoiceFailedDetails(engine=engine, reason_code=reason))


def _bucket(ms: int) -> str:
    return "lt_5s" if ms < 5000 else ("5_15s" if ms < 15000 else "15_30s")


class ReadbackBody(BaseModel):
    """Reviewer decision on one candidate. No free-text fields (tests/privacy/test_boundary.py)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    outcome: readback.Outcome
    field: Literal["temp", "spo2", "pulse", "resp_rate", "bp", "age", "pregnancy"] | None = None  # corrected only
    value: float | None = Field(default=None, allow_inf_nan=False)
    value2: float | None = Field(default=None, allow_inf_nan=False)  # diastolic, for bp
    unit: Literal["c", "f", "years"] | None = None
    # Optimistic concurrency: the resolution the reviewer was looking at (null if none). A decision
    # made on a stale screen is refused instead of silently replacing a newer one.
    supersedes: uuid.UUID | None = None


# ── feature gates (no data touched) ──────────────────────────────────────────────────────────────


def check_engine(settings: Settings, engine: EngineName, language: Language) -> None:
    if engine == "cloud":
        if not settings.voice_cloud_stt_enabled or not settings.sarvam_configured:
            raise ApiError(503, "CLOUD_STT_UNAVAILABLE", "Cloud speech-to-text is not enabled on this server", {"reason": "cloud_not_enabled"})
    else:
        if not settings.voice_local_asr_enabled:
            raise ApiError(503, "LOCAL_ASR_UNAVAILABLE", "On-device speech-to-text is not enabled on this server", {"reason": "local_not_enabled"})
        if not engines.local_supports(language):
            raise ApiError(422, "LANGUAGE_UNSUPPORTED", "The selected engine does not support this language", {"reason": "language_unsupported_by_engine"})
        if not engines.local_model_installed(settings.voice_local_model_dir):
            raise ApiError(503, "LOCAL_ASR_UNAVAILABLE", "The on-device speech model is not installed", {"reason": "local_model_not_installed"})


def _engine_error(engine: EngineName, err: engines.EngineError) -> ApiError:
    if err.reason == "language_unsupported_by_engine":
        return ApiError(422, "LANGUAGE_UNSUPPORTED", "The selected engine does not support this language", {"reason": err.reason})
    code = "CLOUD_STT_UNAVAILABLE" if engine == "cloud" else "LOCAL_ASR_UNAVAILABLE"
    return ApiError(err.status, code, "Speech-to-text failed; no transcript was produced", {"reason": err.reason})


# ── access helpers ───────────────────────────────────────────────────────────────────────────────


async def _load_for_view(conn, principal: Principal, case_id: str):
    """Transcripts are clinical content: visible to the case creator or a medical officer (not to
    supervisors, who audit metadata only)."""
    try:
        return await consent.load_case(conn, principal, case_id, "write")
    except ApiError:
        return await consent.load_case(conn, principal, case_id, "triage")


# ── transcription ────────────────────────────────────────────────────────────────────────────────


async def transcribe(
    conn: aiosqlite.Connection,
    principal: Principal,
    case_id: str,
    clip: AudioClip,
    language: Language,
    engine: EngineName,
    idempotency_key: str,
    settings: Settings,
    request_id: str | None = None,
    cloud_transport=None,
) -> dict:
    check_engine(settings, engine, language)
    purposes = ("triage", "voice_cloud") if engine == "cloud" else ("triage",)

    # T1: access, consent, idempotency — before any engine runs or any audio leaves.
    denial: consent.ConsentNotEffective | None = None
    existing = None
    transcription_id = str(uuid.uuid4())
    authz_seq = 0
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "write")
            snap = None
            for p in purposes:
                snap = await consent.require(conn, case_id, p)
            assert snap is not None
            authz_seq = snap.authz_seq
            async with conn.execute("SELECT * FROM voice_transcriptions WHERE case_id = ? AND idempotency_key = ?", (case_id, idempotency_key)) as cur:
                existing = await cur.fetchone()
            if existing is None:
                await conn.execute(
                    "INSERT INTO voice_transcriptions (transcription_id, case_id, created_by, idempotency_key, audio_sha256, language, engine, status, audio_duration_ms, consent_seq, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (transcription_id, case_id, principal.user_id, idempotency_key, clip.sha256, language, engine, clip.duration_ms, authz_seq, _now()),
                )
                # Committed BEFORE the engine runs, so a cloud upload is on record even if the request
                # dies afterwards (crash, cancellation).
                await audit.record(conn, principal=principal, action="voice_transcription_started", outcome="success", case_id=case_id, request_id=request_id,
                                   details=audit.VoiceStartedDetails(transcription_id=transcription_id, engine=engine, language=language))
            elif existing["status"] == "pending" and _age_s(existing["created_at"]) > PENDING_TTL_S:
                await _finalize_failed(conn, principal, case_id, existing["transcription_id"], existing["engine"], "abandoned", request_id)
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    if existing is not None:
        if existing["created_by"] != principal.user_id or existing["audio_sha256"] != clip.sha256 or existing["language"] != language or existing["engine"] != engine:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This request key was already used for different audio or settings")
        view = await transcription_view(conn, existing["transcription_id"])
        if view["status"] == "pending":
            raise ApiError(409, "IN_PROGRESS", "This recording is still being processed")
        return view  # stored result (including `failed`): a retry never re-runs an engine — use a new key

    try:
        return await _process(conn, principal, case_id, clip, language, engine, settings, request_id, cloud_transport, transcription_id, authz_seq, purposes)
    except BaseException:
        # Interrupted (cancellation, T2 failure, crash in this handler): never leave the row pending.
        with anyio.CancelScope(shield=True):
            try:
                async with transaction(conn):
                    await _finalize_failed(conn, principal, case_id, transcription_id, engine, "interrupted", request_id)
            except Exception:  # noqa: BLE001 - best effort; PENDING_TTL_S covers what this cannot
                pass
        raise


async def _process(conn, principal, case_id, clip, language, engine, settings, request_id, cloud_transport, transcription_id, authz_seq, purposes) -> dict:

    # Outside any transaction: VAD, then the selected engine (no fallback).
    segments: list = []
    status = "completed"
    result: engines.SttResult | None = None
    failure: engines.EngineError | None = None
    try:
        if settings.voice_vad_enabled:
            from app.voice import vad

            try:
                segments = await anyio.to_thread.run_sync(vad.detect, clip)
            except vad.VadUnavailable:
                raise engines.EngineError("vad_not_installed") from None
            if not segments:
                status = "no_speech"
        if status == "completed" and engine == "cloud":
            # Re-check consent right before audio leaves the server: a withdrawal during VAD must stop the
            # upload, not only discard the result. (A withdrawal after this point can only discard it, at T2.)
            snap = await consent.snapshot(conn, case_id)
            if snap.authz_seq != authz_seq or not all(snap.is_effective(p) for p in purposes):
                status = "consent_changed_before_upload"
        if status == "completed":
            if engine == "cloud":
                result = await anyio.to_thread.run_sync(
                    lambda: engines.transcribe_cloud(clip, language, base_url=settings.sarvam_base_url, api_key=settings.sarvam_api_key, timeout_s=settings.sarvam_timeout_s, transport=cloud_transport)
                )
            else:
                result = await anyio.to_thread.run_sync(lambda: engines.transcribe_local(clip, language, model_dir=settings.voice_local_model_dir))
            if not any(ch.isalnum() for ch in result.text):  # blank, or only marks/punctuation (e.g. a lone "े" from noise)
                status = "empty_transcript"
    except engines.EngineError as exc:
        failure = exc
    except Exception:  # noqa: BLE001 - never leave the row `pending`; recorded as an explicit failure
        failure = engines.EngineError("internal_error", 500)

    # T2: finalise only if consent is unchanged since T1.
    candidates = extract.extract(result.text) if (result and status == "completed") else []
    consent_changed = False
    abandoned = False
    async with transaction(conn):
        snap = await consent.snapshot(conn, case_id)
        consent_changed = snap.authz_seq != authz_seq or not all(snap.is_effective(p) for p in purposes)
        row = await (await conn.execute("SELECT status FROM voice_transcriptions WHERE transcription_id = ?", (transcription_id,))).fetchone()
        if row is None or row[0] != "pending":
            # A retry marked this row abandoned (PENDING_TTL_S) while it was still running. The row is final
            # (append-only), so this late result is dropped with a clear error instead of a 500.
            abandoned = True
        elif consent_changed or failure is not None:
            reason = "consent_changed" if consent_changed else failure.reason  # type: ignore[union-attr]
            await _finalize_failed(conn, principal, case_id, transcription_id, engine, reason, request_id, outcome="denied" if consent_changed else "failure")
        else:
            await conn.execute(
                "UPDATE voice_transcriptions SET status = ?, model_id = ?, mode = ?, segments_json = ?, transcript_raw = ?, completed_at = ? WHERE transcription_id = ?",
                (
                    status,
                    result.model_id if result else None,
                    result.mode if result else None,
                    json.dumps([[round(s.start_s, 3), round(s.end_s, 3)] for s in segments]),
                    result.text if result else None,
                    _now(),
                    transcription_id,
                ),
            )
            for ordinal, c in enumerate(candidates):
                await conn.execute(
                    "INSERT INTO voice_candidates (candidate_id, transcription_id, case_id, ordinal, field, char_start, char_end, raw_value, raw_value2, unit, normalized_json, flags_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), transcription_id, case_id, ordinal, c.field, c.char_start, c.char_end, c.raw_value, c.raw_value2, c.unit,
                     json.dumps(c.normalized) if c.normalized is not None else None, json.dumps(c.flags), _now()),
                )
            await audit.record(
                conn, principal=principal, action="voice_transcribed", outcome="success", case_id=case_id, request_id=request_id,
                details=audit.VoiceTranscribedDetails(transcription_id=transcription_id, engine=engine, language=language, status=status,  # type: ignore[arg-type]
                                                      duration_bucket=_bucket(clip.duration_ms), segment_count=len(segments), candidate_count=len(candidates)),  # type: ignore[arg-type]
            )
    if abandoned:
        raise ApiError(409, "TRANSCRIPTION_ABANDONED", "This recording took too long and was closed; please record again")
    if consent_changed:
        raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed while the recording was being processed; the result was discarded")
    if failure is not None:
        raise _engine_error(engine, failure)
    view = await transcription_view(conn, transcription_id)
    if result and result.warnings:
        view["warnings"] = list(result.warnings)
    return view


# ── views ────────────────────────────────────────────────────────────────────────────────────────


async def _latest_resolutions(conn, where: str, params: tuple) -> dict[str, aiosqlite.Row]:
    async with conn.execute(
        f"SELECT e.* FROM voice_readback_events e JOIN (SELECT candidate_id, max(seq) AS seq FROM voice_readback_events WHERE {where} GROUP BY candidate_id) m "
        "ON e.seq = m.seq",
        params,
    ) as cur:
        return {r["candidate_id"]: r for r in await cur.fetchall()}


def _candidate_view(row, transcript: str, language: str, resolution) -> dict:
    normalized = json.loads(row["normalized_json"]) if row["normalized_json"] else None
    return {
        "candidate_id": row["candidate_id"],
        "field": row["field"],
        "char_start": row["char_start"],
        "char_end": row["char_end"],
        "heard_text": transcript[row["char_start"]:row["char_end"]],
        "raw_value": row["raw_value"],
        "raw_value2": row["raw_value2"],
        "unit": row["unit"],
        "normalized": normalized,
        "flags": json.loads(row["flags_json"]),
        "display": readback.display_value(row["field"], row["raw_value"], row["raw_value2"], row["unit"], normalized, language),
        "readback_text": readback.readback_text(row["field"], row["raw_value"], row["raw_value2"], row["unit"], normalized, language,
                                                heard=transcript[row["char_start"]:row["char_end"]], flags=json.loads(row["flags_json"])),
        "can_confirm": readback.can_confirm(row["field"], normalized, json.loads(row["flags_json"])),
        "resolution": None if resolution is None else {
            "event_id": resolution["event_id"],
            "outcome": resolution["outcome"],
            "field": resolution["resolved_field"],
            "values": json.loads(resolution["resolved_json"]) if resolution["resolved_json"] else None,
            "unit": resolution["resolved_unit"],
            "actor_role": resolution["actor_role"],
            "at": resolution["created_at"],
        },
    }


async def transcription_view(conn, transcription_id: str) -> dict:
    async with conn.execute("SELECT * FROM voice_transcriptions WHERE transcription_id = ?", (transcription_id,)) as cur:
        t = await cur.fetchone()
    if t is None:
        raise not_found()
    async with conn.execute("SELECT * FROM voice_candidates WHERE transcription_id = ? ORDER BY ordinal", (transcription_id,)) as cur:
        rows = await cur.fetchall()
    resolutions = await _latest_resolutions(conn, "case_id = ?", (t["case_id"],))
    transcript = t["transcript_raw"] or ""
    return {
        "transcription_id": t["transcription_id"],
        "case_id": t["case_id"],
        "status": t["status"],
        "failure_code": t["failure_code"],
        "language": t["language"],
        "engine": t["engine"],
        "processing": "local inference (no provider call)" if t["engine"] == "local" else "cloud: Sarvam AI",
        "model_id": t["model_id"],
        "mode": t["mode"],
        "audio_duration_ms": t["audio_duration_ms"],
        "speech_segments": json.loads(t["segments_json"]),
        "transcript_raw": t["transcript_raw"],
        "transcript_status": "machine transcript — not checked",
        "readback_template_status": readback.TEMPLATE_REVIEW_STATUS[t["language"]],
        "keyword_list_status": extract.KEYWORD_REVIEW_STATUS[t["language"]],
        "candidates": [_candidate_view(r, transcript, t["language"], resolutions.get(r["candidate_id"])) for r in rows],
        "warnings": [],
        "created_at": t["created_at"],
    }


async def _require_triage_for_read(conn, principal, case_id: str, request_id: str | None) -> None:
    """Transcripts are shown only while triage consent is in effect (withdrawal stops further use;
    the stored rows are kept, as the notice says, until retention/deletion is implemented)."""
    await _load_for_view(conn, principal, case_id)
    snap = await consent.snapshot(conn, case_id)
    if not snap.is_effective("triage"):
        state = snap.effective("triage")
        raise await consent.audit_denied(conn, principal, case_id, consent.ConsentNotEffective("triage", "not_provided" if state == "granted" else state), request_id)


async def get_transcription(conn, principal, case_id: str, transcription_id: str, request_id: str | None = None) -> dict:
    await _require_triage_for_read(conn, principal, case_id, request_id)
    view = await transcription_view(conn, transcription_id)
    if view["case_id"] != case_id:
        raise not_found()
    return view


async def list_transcriptions(conn, principal, case_id: str, request_id: str | None = None) -> list[dict]:
    await _require_triage_for_read(conn, principal, case_id, request_id)
    async with conn.execute("SELECT transcription_id FROM voice_transcriptions WHERE case_id = ? ORDER BY created_at", (case_id,)) as cur:
        ids = [r["transcription_id"] for r in await cur.fetchall()]
    return [await transcription_view(conn, i) for i in ids]


# ── read-back decisions ──────────────────────────────────────────────────────────────────────────


async def resolve_readback(conn, principal: Principal, case_id: str, candidate_id: str, body: ReadbackBody, request_id: str | None = None) -> dict:
    """Only the case reviewer (creator ANM or a medical officer — `triage` access) may decide."""
    denial = None
    transcription_id = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
            async with conn.execute(
                "SELECT c.*, t.status AS t_status FROM voice_candidates c JOIN voice_transcriptions t USING (transcription_id) WHERE c.candidate_id = ? AND c.case_id = ?",
                (candidate_id, case_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None or row["t_status"] != "completed":
                raise not_found()
            async with conn.execute("SELECT event_id FROM voice_readback_events WHERE candidate_id = ? ORDER BY seq DESC LIMIT 1", (candidate_id,)) as cur:
                latest = await cur.fetchone()
            if (latest["event_id"] if latest else None) != (str(body.supersedes) if body.supersedes else None):
                raise ApiError(409, "STALE_DECISION", "This value was decided by someone else in the meantime; reload and review again")
            normalized = json.loads(row["normalized_json"]) if row["normalized_json"] else None
            resolved_field, values, unit = row["field"], None, None
            if body.outcome == "confirmed":
                if body.field or body.value is not None or body.value2 is not None or body.unit:
                    raise ApiError(400, "VALIDATION_ERROR", "A confirmation carries no values")
                if not readback.can_confirm(row["field"], normalized, json.loads(row["flags_json"])):
                    raise ApiError(409, "CORRECTION_REQUIRED", "This value could not be interpreted; correct, reject or mark it unsure")
                values, unit = normalized, row["unit"]
            elif body.outcome == "corrected":
                resolved_field = body.field or row["field"]
                try:
                    values = readback.normalize_correction(resolved_field, body.value, body.value2, body.unit)
                except readback.CorrectionInvalid as exc:
                    raise ApiError(400, "CORRECTION_INVALID", "The corrected value is not valid for this field", {"reason": exc.reason}) from None
                unit = body.unit
            else:
                if body.field or body.value is not None or body.value2 is not None or body.unit:
                    raise ApiError(400, "VALIDATION_ERROR", "Rejected or unsure decisions carry no values")
            await conn.execute(
                "INSERT INTO voice_readback_events (event_id, candidate_id, case_id, outcome, resolved_field, resolved_json, resolved_unit, actor_id, actor_role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), candidate_id, case_id, body.outcome, resolved_field, json.dumps(values) if values is not None else None, unit, principal.user_id, principal.role.value, _now()),
            )
            await audit.record(conn, principal=principal, action="voice_readback_resolved", outcome="success", case_id=case_id, request_id=request_id,
                               details=audit.VoiceReadbackDetails(candidate_id=candidate_id, field=resolved_field, outcome=body.outcome))
            transcription_id = row["transcription_id"]
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    view = await transcription_view(conn, transcription_id)  # type: ignore[arg-type]
    return next(c for c in view["candidates"] if c["candidate_id"] == candidate_id)


# ── prefill ──────────────────────────────────────────────────────────────────────────────────────


async def prefill(conn, principal: Principal, case_id: str, request_id: str | None = None) -> dict:
    """Reviewer-confirmed values shaped for the existing triage input. Never submits triage."""
    denial = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    async with read_transaction(conn):  # one consistent snapshot, re-checking consent inside it
        if not (await consent.snapshot(conn, case_id)).is_effective("triage"):
            raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed; reload")
        resolutions = await _latest_resolutions(conn, "case_id = ?", (case_id,))
        async with conn.execute(
            "SELECT c.*, t.segments_json FROM voice_candidates c JOIN voice_transcriptions t USING (transcription_id) WHERE c.case_id = ? ORDER BY t.created_at, c.ordinal",
            (case_id,),
        ) as cur:
            cands = {r["candidate_id"]: r for r in await cur.fetchall()}
    resolved = []
    unresolved = []
    for cid, c in cands.items():
        ev = resolutions.get(cid)
        if c["field"] not in readback.PREFILL_FIELDS and c["field"] != "unassigned":
            continue
        if ev is None or ev["outcome"] == "unsure":
            unresolved.append({"candidate_id": cid, "field": c["field"], "transcription_id": c["transcription_id"], "state": "undecided" if ev is None else "unsure"})
            continue
        segs = json.loads(c["segments_json"])
        resolved.append({
            "field": ev["resolved_field"],
            "outcome": ev["outcome"],
            "values": json.loads(ev["resolved_json"]) if ev["resolved_json"] and ev["resolved_field"] in readback.PREFILL_FIELDS else None,
            "source": {
                "type": "voice_manual_correction" if ev["outcome"] == "corrected" else "voice_transcript",
                "transcription_id": c["transcription_id"],
                "candidate_id": cid,
                "transcript_chars": [c["char_start"], c["char_end"]],
                # Speech span of the whole clip (VAD). Engines give no reliable per-word timing, so the
                # exact evidence is the highlighted transcript span, not an audio timestamp.
                "clip_speech_sec": [segs[0][0], segs[-1][1]] if segs else None,
                "readback_outcome": ev["outcome"],
                "resolved_by_role": ev["actor_role"],
            },
        })
    out = readback.assemble_prefill(resolved)
    out["unresolved"] = unresolved
    out["case_id"] = case_id
    out["note"] = "Pre-fill only. Review and submit triage separately; missing, rejected or unsure values stay missing and need human review."
    return out
