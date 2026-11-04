"""Database persistence and audit services for Gemini 2.0 Multimodal Live Duplex Follow-up (docs/20).

Records duplex voice sessions, field extractions with patient vernacular speech phrases,
and maintains strict provenance with status `pending_review`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import aiosqlite

from app import audit
from app.auth import Principal
from app.database import transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


async def create_live_session(
    conn: aiosqlite.Connection,
    case_id: str,
    principal: Principal,
    language: Literal["hi", "or"],
    consent_seq: int,
    request_id: str | None = None,
) -> str:
    """Records the initiation of a Duplex Voice Follow-up session."""
    session_id = str(uuid.uuid4())
    now = _now()
    async with transaction(conn):
        await conn.execute(
            """INSERT INTO gemini_live_sessions 
            (session_id, case_id, language, actor_id, actor_role, consent_seq, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (session_id, case_id, language, principal.user_id, principal.role.value, consent_seq, now),
        )
        await audit.record(
            conn,
            principal=principal,
            action="gemini_live_session_started",
            outcome="success",
            case_id=case_id,
            request_id=request_id,
            details=audit.GeminiLiveSessionDetails(session_id=session_id, language=language),
        )
    return session_id


async def record_live_field(
    conn: aiosqlite.Connection,
    session_id: str,
    case_id: str,
    principal: Principal,
    field_name: str,
    value: str,
    unit: str | None,
    patient_phrase: str | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Records an extracted missing field from patient speech during duplex live follow-up."""
    field_id = str(uuid.uuid4())
    now = _now()
    async with transaction(conn):
        await conn.execute(
            """INSERT INTO gemini_live_fields
            (field_id, session_id, case_id, field_name, value, unit, patient_phrase, source, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'gemini_live_duplex', 'pending_review', ?)""",
            (field_id, session_id, case_id, field_name, value, unit, patient_phrase, now),
        )
        await audit.record(
            conn,
            principal=principal,
            action="gemini_live_field_extracted",
            outcome="success",
            case_id=case_id,
            request_id=request_id,
            details=audit.GeminiLiveFieldDetails(
                session_id=session_id,
                field_id=field_id,
                field_name=field_name,
            ),
        )

    return {
        "field_id": field_id,
        "session_id": session_id,
        "case_id": case_id,
        "field_name": field_name,
        "value": value,
        "unit": unit,
        "patient_phrase": patient_phrase,
        "source": "gemini_live_duplex",
        "status": "pending_review",
        "created_at": now,
    }


async def complete_live_session(
    conn: aiosqlite.Connection,
    session_id: str,
    reason: str = "completed",
    request_id: str | None = None,
) -> None:
    """Finalizes an active duplex follow-up session."""
    now = _now()
    async with transaction(conn):
        await conn.execute(
            "UPDATE gemini_live_sessions SET status = 'completed', finish_reason = ?, completed_at = ? WHERE session_id = ? AND status = 'active'",
            (reason, now, session_id),
        )


async def list_live_fields(conn: aiosqlite.Connection, case_id: str) -> list[dict[str, Any]]:
    """Returns all duplex voice-extracted fields for a given case."""
    async with conn.execute(
        """SELECT field_id, session_id, case_id, field_name, value, unit, patient_phrase, source, status, created_at
        FROM gemini_live_fields WHERE case_id = ? ORDER BY created_at ASC""",
        (case_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
