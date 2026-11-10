"""Interview answers (docs/plans/scripted-voice-interviewer.md §4–5). Synthetic data only in this prototype.

- Recording is the visit's ANM or handler (`load_case(..., "write")`) with triage consent in effect, and an explicit
  `confirm: true`: nothing is ever selected or saved on the health worker's behalf.
- An answer is a choice from the question's fixed options or short text, typed or taken from a transcript of this
  case (with the character span). Only patient questions can be answered; health-worker tasks (measurements, the
  red-flag and danger-sign screens) are recorded on the triage form, never here.
- Answers are append-only; the latest per question counts. "Not sure" / "not checked yet" is kept as said and the
  information stays missing.
- Nothing here writes a triage run, an urgency, a vital sign or a completed screen.
- Reading: the visit's ANM or a medical officer see answers while triage consent is in effect; a supervisor sees the
  questions and whether each was answered, never the answer (owner decision D-B). The audit log gets keys and enums.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app import audit, consent
from app.auth import Principal, Role
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.interview import script
from app.rules.models import TriageInput

NOTICE = ("Fixed questions for information the triage form is still missing. Answers are what was said, checked by the "
          "health worker. This never sets urgency, never records a vital sign and never completes the red-flag screen: "
          "use the triage form for those, and tell the medical officer about any danger sign.")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcription_id: uuid.UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    question_key: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    choice: str | None = Field(default=None, max_length=40, pattern=r"^[a-z0-9 -]{1,40}$")  # one of the fixed options, never free text
    text: str | None = Field(default=None, max_length=script.TEXT_MAX)
    source: Source | None = None
    speaker: Literal["patient", "caregiver", "anm"]
    confirm: Literal[True]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


async def _latest_input(conn, case_id: str) -> TriageInput | None:
    async with conn.execute("SELECT input_json FROM triage_runs WHERE case_id = ? AND input_json IS NOT NULL ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
        r = await cur.fetchone()
    return TriageInput.model_validate_json(r["input_json"]) if r is not None else None


async def _latest_answers(conn, case_id: str) -> dict[str, aiosqlite.Row]:
    async with conn.execute(
        "SELECT * FROM interview_answer_events e WHERE case_id = ? AND seq = "
        "(SELECT MAX(seq) FROM interview_answer_events x WHERE x.case_id = e.case_id AND x.question_key = e.question_key)", (case_id,)) as cur:
        return {r["question_key"]: r for r in await cur.fetchall()}


def _answer_view(q: script.Question, r: aiosqlite.Row | None) -> dict | None:
    if r is None:
        return None
    return {"choice": r["choice_value"], "text": r["text_value"], "source": r["source"], "transcription_id": r["transcription_id"],
            "char_start": r["char_start"], "char_end": r["char_end"], "speaker": r["speaker"], "is_unknown": script.is_unknown(q, r["choice_value"]),
            "recorded_by_role": r["actor_role"], "recorded_at": r["created_at"], "status": "checked_by_health_worker"}


def _still_missing(questions: list[script.Question], answers: dict) -> list[str]:
    """Danger-sign screens and measurements stay missing until the triage form has them; a patient question is
    missing until it has an answer that is not "not sure"."""
    out = []
    for q in questions:
        a = answers.get(q.key)
        if not q.answerable or a is None or script.is_unknown(q, a["choice_value"]):
            out.append(q.key)
    return out


async def view(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    denial = None
    async with read_transaction(conn):
        try:
            case = await consent.load_case(conn, principal, case_id, "write", probe=True)
        except ApiError:
            case = await consent.load_case(conn, principal, case_id, "read")
        snap = await consent.snapshot(conn, case_id)
        if not snap.is_effective("triage"):
            state = snap.effective("triage")
            denial = consent.ConsentNotEffective("triage", "not_provided" if state == "granted" else state)
        else:
            questions = script.build(case["scenario"], await _latest_input(conn, case_id))
            answers = await _latest_answers(conn, case_id)
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    hidden = principal.role == Role.SUPERVISOR  # metadata only (D-B): whether answered, never the answer
    return {
        "case_id": case_id, "script_version": script.SCRIPT_VERSION, "language": script.LANGUAGE, "notice": NOTICE,
        "answers_hidden": hidden,
        "questions": [{"key": q.key, "label": q.label, "question_text": q.question_text, "kind": q.kind, "options": list(q.options),
                       "audience": q.audience, "answerable": q.answerable, "is_danger_sign": q.is_danger_sign,
                       "answered": q.key in answers, "answer": None if hidden else _answer_view(q, answers.get(q.key))} for q in questions],
        "still_missing": _still_missing(questions, answers),
    }


async def _has_identifier(text: str) -> bool:
    """The same heuristic as the review reasons and AI-field corrections (docs/16 §8): identifier patterns, then the
    full redaction pass (Presidio NER + patterns). A text answer is stored, so identifiers and names are refused."""
    import anyio

    from app.privacy.pii import _process_raw, residual_hit

    if residual_hit(text):
        return True
    outcome = await anyio.to_thread.run_sync(_process_raw, text)
    return outcome.redacted is None or outcome.redacted.redacted_total > 0


async def record(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: AnswerBody, request_id=None) -> dict:
    if principal.role != Role.ANM:
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    if body.text is not None and body.text.strip() and await _has_identifier(body.text):
        raise ApiError(422, "PII_DETECTED", "The answer looks like it contains a name or an identifier; record what was said about the problem only")
    denial = None
    out = None
    try:
        async with transaction(conn):
            case = await consent.load_case(conn, principal, case_id, "write")
            snap = await consent.require(conn, case_id, "triage")
            q = next((x for x in script.build(case["scenario"], await _latest_input(conn, case_id)) if x.key == body.question_key), None)
            if q is None:
                raise ApiError(409, "NOT_IN_SCRIPT", "This question is not in the current interview")
            if not q.answerable:
                raise ApiError(409, "RECORD_ON_TRIAGE_FORM", "Measurements and danger-sign screens are recorded on the triage form")
            text = body.text.strip() if body.text is not None else None
            if q.kind == "choice" and (body.choice not in q.options or text is not None):
                raise ApiError(400, "ANSWER_INVALID", "Choose one of the listed answers", {"reason": "choice_required"})
            if q.kind == "text" and (not text or body.choice is not None):
                raise ApiError(400, "ANSWER_INVALID", "Enter the answer as said", {"reason": "text_required"})
            src = body.source
            if src is not None:
                async with conn.execute("SELECT transcript_raw, status FROM voice_transcriptions WHERE transcription_id = ? AND case_id = ?",
                                        (str(src.transcription_id), case_id)) as cur:
                    t = await cur.fetchone()
                if t is None or t["status"] != "completed":
                    raise not_found()
                if src.char_end <= src.char_start or src.char_end > len(t["transcript_raw"] or ""):
                    raise ApiError(400, "ANSWER_INVALID", "The quoted part is outside the transcript", {"reason": "span_out_of_range"})
            event_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO interview_answer_events (event_id, case_id, question_key, script_version, kind, choice_value, text_value, source, "
                "transcription_id, char_start, char_end, speaker, consent_seq, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, case_id, q.key, script.SCRIPT_VERSION, q.kind, body.choice if q.kind == "choice" else None, text if q.kind == "text" else None,
                 "transcript" if src else "typed", str(src.transcription_id) if src else None, src.char_start if src else None,
                 src.char_end if src else None, body.speaker, snap.latest_seq["triage"], principal.user_id, principal.role.value, _now()),
            )
            await audit.record(conn, principal=principal, action="interview_answer_recorded", outcome="success", case_id=case_id, request_id=request_id,
                               details=audit.InterviewAnswerDetails(question_key=q.key, kind=q.kind, source="transcript" if src else "typed",
                                                                    speaker=body.speaker, is_unknown=script.is_unknown(q, body.choice)))
            out = {"event_id": event_id, "question_key": q.key, "recorded": True}
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    return out  # type: ignore[return-value]

