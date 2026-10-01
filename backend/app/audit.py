"""Application-level append-only, hash-chained audit log (docs/11 §H).

Guarantees (and limits):
- Rows are append-only at the application layer (SQLite triggers reject UPDATE/DELETE).
- Each row's hash covers its content plus the previous row's hash, so edits made without recomputing
  the chain are detected by `verify_chain`. This is tamper-evident, not immutable: a database-file
  administrator can drop triggers or rewrite the file, and removal of the newest rows is not detectable
  without an external checkpoint (future work).
- Details avoid direct identifiers and raw clinical content by design, but actor_id, case_id,
  request_id and timestamps link events to accounts and cases and may still be personal data.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app.auth import Principal
from app.database import read_transaction

GENESIS_HASH = "0" * 64

AuditAction = Literal[
    "case_created",
    "consent_granted",
    "consent_declined",
    "consent_withdrawn",
    "consent_denied",
    "triage_recorded",
    "pii_redacted",
    "ai_request_blocked",
    "ai_output_discarded",
    "ai_output_returned",
    "audit_verified",
    "voice_transcription_started",
    "voice_transcribed",
    "voice_transcription_failed",
    "voice_readback_resolved",
    "voice_tts_generated",
    "ocr_document_started",
    "ocr_document_processed",
    "ocr_document_failed",
    "ocr_attestation_recorded",
    "ocr_review_resolved",
    "ocr_document_deleted",
]
Outcome = Literal["success", "denied", "failure"]


class _Details(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CaseCreatedDetails(_Details):
    scenario: str
    facility_code: str


ConsentPurpose = Literal["triage", "ai_assist", "voice_cloud"]


class ConsentChangeDetails(_Details):
    purposes: list[ConsentPurpose]
    notice_version: str
    language: Literal["en", "hi", "or"]
    method: Literal["patient_button", "staff_attested_verbal", "cascade_from_triage"]


class ConsentDeniedDetails(_Details):
    purpose: ConsentPurpose
    state: Literal["not_provided", "declined", "withdrawn"]


class TriageRecordedDetails(_Details):
    run_id: str
    urgency: Literal["RED", "YELLOW", "GREEN"]
    rule_ids: list[str]
    engine_version: str
    ruleset_version: str


class PiiRedactedDetails(_Details):
    redacted_total: int = Field(ge=0)


class ReasonDetails(_Details):
    reason_code: str = Field(pattern=r"^[a-z_]{1,48}$")


class VoiceTranscribedDetails(_Details):
    """Counts and categories only: never transcript text, values, or audio."""

    transcription_id: str
    engine: Literal["local", "cloud"]
    language: Literal["en", "hi", "or"]
    status: Literal["completed", "no_speech", "empty_transcript"]
    duration_bucket: Literal["lt_5s", "5_15s", "15_30s"]
    segment_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)


class VoiceStartedDetails(_Details):
    transcription_id: str
    engine: Literal["local", "cloud"]
    language: Literal["en", "hi", "or"]


class VoiceFailedDetails(_Details):
    engine: Literal["local", "cloud"]
    reason_code: str = Field(pattern=r"^[a-z_]{1,48}$")


class VoiceReadbackDetails(_Details):
    candidate_id: str
    field: str = Field(pattern=r"^[a-z_0-9]{1,24}$")
    outcome: Literal["confirmed", "corrected", "rejected", "unsure"]


class VoiceTtsDetails(_Details):
    language: Literal["en", "hi", "or"]
    ok: bool


OcrDocType = Literal["lab_report", "prescription", "discharge_summary"]


class OcrStartedDetails(_Details):
    """Ids, enums and size buckets only: never document text, values or images."""

    document_id: str
    document_type: OcrDocType
    media_type: Literal["image/png", "image/jpeg", "application/pdf"]
    size_bucket: Literal["lt_1mb", "1_5mb", "5_20mb"]


class OcrProcessedDetails(_Details):
    document_id: str
    document_type: OcrDocType
    status: Literal["completed", "quality_rejected", "no_text"]
    page_count: int = Field(ge=0)
    field_count: int = Field(ge=0)
    disputed_count: int = Field(ge=0)
    engines: list[Literal["paddleocr", "surya", "chandra"]]


class OcrFailedDetails(_Details):
    document_id: str
    reason_code: str = Field(pattern=r"^[a-z_]{1,48}$")


class OcrAttestationDetails(_Details):
    document_id: str
    answer: Literal["matches", "does_not_match", "unsure"]


class OcrReviewDetails(_Details):
    field_id: str
    kind: Literal["lab", "medication"]
    outcome: Literal["confirmed", "corrected", "rejected", "unsure"]


class OcrDeletedDetails(_Details):
    document_id: str
    reason: Literal["reviewer_request", "retention_expired"]
    files_removed: int = Field(ge=0)
    files_failed: int = Field(ge=0)


class AuditVerifiedDetails(_Details):
    verified_through_seq: int = Field(ge=0)
    ok: bool


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_hash(row: dict, previous_hash: str) -> str:
    payload = {k: row[k] for k in ("seq", "event_id", "timestamp", "actor_id", "actor_role", "action", "case_id", "outcome", "request_id", "details_json")}
    return hashlib.sha256((_canonical(payload) + previous_hash).encode()).hexdigest()


async def record(
    conn: aiosqlite.Connection,
    *,
    principal: Principal,
    action: AuditAction,
    outcome: Outcome,
    details: _Details,
    case_id: str | None = None,
    request_id: str | None = None,
) -> int:
    """Append one event. Must run inside the caller's `transaction()` so it commits (or rolls back)
    together with the change it describes; seq is computed under the write lock."""
    if not conn.in_transaction:
        raise RuntimeError("audit.record must be called inside a write transaction")
    async with conn.execute("SELECT seq, current_hash FROM audit_log ORDER BY seq DESC LIMIT 1") as cur:
        last = await cur.fetchone()
    seq = (last["seq"] + 1) if last else 1
    previous_hash = last["current_hash"] if last else GENESIS_HASH
    row = {
        "seq": seq,
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "actor_id": principal.user_id,
        "actor_role": principal.role.value,
        "action": action,
        "case_id": case_id,
        "outcome": outcome,
        "request_id": request_id,
        "details_json": _canonical(details.model_dump(mode="json")),
    }
    current_hash = compute_hash(row, previous_hash)
    await conn.execute(
        "INSERT INTO audit_log (seq, event_id, timestamp, actor_id, actor_role, action, case_id, outcome, request_id, details_json, previous_hash, current_hash) "
        "VALUES (:seq, :event_id, :timestamp, :actor_id, :actor_role, :action, :case_id, :outcome, :request_id, :details_json, :previous_hash, :current_hash)",
        {**row, "previous_hash": previous_hash, "current_hash": current_hash},
    )
    return seq


class VerifyResult(BaseModel):
    ok: bool
    verified_through_seq: int
    first_bad_seq: int | None = None


async def verify_chain(conn: aiosqlite.Connection) -> VerifyResult:
    """Recompute the chain over a consistent snapshot. Detects edits, middle deletions and broken
    links; cannot detect truncation of the newest rows (no external checkpoint)."""
    async with read_transaction(conn):
        async with conn.execute("SELECT * FROM audit_log ORDER BY seq") as cur:
            rows = list(await cur.fetchall())
    previous_hash = GENESIS_HASH
    for expected_seq, r in enumerate(rows, start=1):
        row = dict(r)
        if row["seq"] != expected_seq or row["previous_hash"] != previous_hash or compute_hash(row, previous_hash) != row["current_hash"]:
            return VerifyResult(ok=False, verified_through_seq=expected_seq - 1, first_bad_seq=row["seq"])
        previous_hash = row["current_hash"]
    return VerifyResult(ok=True, verified_through_seq=len(rows))
