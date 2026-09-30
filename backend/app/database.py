"""SQLite storage (hackathon). Tables per docs/09_Implementation_Todo_List.md §1.2."""

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite

from app.config import get_settings

_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS cases (
    case_id        TEXT PRIMARY KEY,
    patient_token  TEXT NOT NULL,
    facility_code  TEXT NOT NULL,
    scenario       TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE IF NOT EXISTS consent (
    consent_id  TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases(case_id),
    method      TEXT NOT NULL,
    language    TEXT NOT NULL,
    audio_ref   TEXT,
    granted_at  TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE IF NOT EXISTS triage_notes (
    case_id      TEXT PRIMARY KEY REFERENCES cases(case_id),
    urgency      TEXT NOT NULL,
    fields_json  TEXT NOT NULL DEFAULT '{{}}',
    scores_json  TEXT NOT NULL DEFAULT '{{}}',
    review_json  TEXT NOT NULL DEFAULT '{{}}',
    created_at   TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id       TEXT PRIMARY KEY,
    timestamp      TEXT NOT NULL DEFAULT {_NOW},
    actor_id       TEXT NOT NULL,
    action         TEXT NOT NULL,
    case_id        TEXT REFERENCES cases(case_id),
    details_json   TEXT NOT NULL DEFAULT '{{}}',
    previous_hash  TEXT,
    current_hash   TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS referrals (
    referral_id    TEXT PRIMARY KEY,
    case_id        TEXT NOT NULL REFERENCES cases(case_id),
    from_facility  TEXT NOT NULL,
    to_facility    TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT {_NOW}
);

CREATE INDEX IF NOT EXISTS idx_consent_case ON consent(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id);
CREATE INDEX IF NOT EXISTS idx_referrals_case ON referrals(case_id);
"""

TABLES = ("cases", "consent", "triage_notes", "audit_events", "referrals")


async def _connect(path: Path) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def init_db(path: Path | None = None) -> None:
    path = path or get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await _connect(path)
    try:
        await conn.executescript(SCHEMA)
        await conn.commit()
    finally:
        await conn.close()


async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """FastAPI dependency: one connection per request."""
    conn = await _connect(get_settings().database_path)
    try:
        yield conn
    finally:
        await conn.close()


async def existing_tables(conn: aiosqlite.Connection) -> set[str]:
    async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
        return {row["name"] async for row in cur}
