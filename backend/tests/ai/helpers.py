"""Shared helpers for Phase 6 tests. Synthetic data only."""

import sqlite3
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from tests.privacy.helpers import auth

INTAKE = ("Patient Ramesh Kumar reports fever for 3 days. BP 150/90, SpO2 91%. "
          "No chest pain. Taking paracetamol 500 mg twice daily.")


def extract(client, token, case_id, text=INTAKE, key=None, **extra):
    body = {"idempotency_key": key or str(uuid.uuid4()), "intake_text": text, **extra}
    return client.post(f"/api/v1/cases/{case_id}/ai/extractions", json=body, headers=auth(token))


def by_field(view) -> dict:
    return {f["field"]: f for f in view["fields"]}


def db():
    c = sqlite3.connect(get_settings().database_path)
    c.row_factory = sqlite3.Row
    return c


def count(table: str, case_id: str | None = None) -> int:
    with db() as c:
        if case_id:
            return c.execute(f"SELECT COUNT(*) FROM {table} WHERE case_id = ?", (case_id,)).fetchone()[0]
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def dump() -> str:
    with db() as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return "\n".join(str(tuple(row)) for t in tables for row in c.execute(f"SELECT * FROM {t}"))


def add_transcript(case_id: str, text: str, language: str = "en", created_by: str = "anm") -> str:
    """Insert a completed voice transcription row directly (the voice engines are not under test here)."""
    tid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with db() as c:
        seq = c.execute("SELECT MAX(seq) FROM consent_events WHERE case_id = ?", (case_id,)).fetchone()[0]
        c.execute(
            "INSERT INTO voice_transcriptions (transcription_id, case_id, created_by, idempotency_key, audio_sha256, language, engine, status, model_id, mode, "
            "audio_duration_ms, segments_json, transcript_raw, consent_seq, created_at, completed_at) VALUES (?, ?, ?, ?, 'x', ?, 'local', 'completed', 'm', 'ctc', 4000, '[]', ?, ?, ?, ?)",
            (tid, case_id, created_by, str(uuid.uuid4()), language, text, seq, now, now),
        )
    return tid
