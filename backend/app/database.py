"""SQLite storage (hackathon). Baseline tables per docs/09 §1.2; privacy tables per docs/11.

Transactions are explicit: connections use autocommit (`isolation_level=None`) and every write goes
through `transaction()` (BEGIN IMMEDIATE ... COMMIT/ROLLBACK). Migrations never use `executescript`,
because it silently commits any pending transaction.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.config import get_settings

_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"

# ── Step 1: baseline schema (Phase 1). Idempotent; unchanged from the original SCHEMA. ──────────
BASELINE_STATEMENTS: tuple[str, ...] = (
    f"""CREATE TABLE IF NOT EXISTS cases (
    case_id        TEXT PRIMARY KEY,
    patient_token  TEXT NOT NULL,
    facility_code  TEXT NOT NULL,
    scenario       TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT {_NOW}
)""",
    f"""CREATE TABLE IF NOT EXISTS consent (
    consent_id  TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases(case_id),
    method      TEXT NOT NULL,
    language    TEXT NOT NULL,
    audio_ref   TEXT,
    granted_at  TEXT NOT NULL DEFAULT {_NOW}
)""",
    f"""CREATE TABLE IF NOT EXISTS triage_notes (
    case_id      TEXT PRIMARY KEY REFERENCES cases(case_id),
    urgency      TEXT NOT NULL,
    fields_json  TEXT NOT NULL DEFAULT '{{}}',
    scores_json  TEXT NOT NULL DEFAULT '{{}}',
    review_json  TEXT NOT NULL DEFAULT '{{}}',
    created_at   TEXT NOT NULL DEFAULT {_NOW}
)""",
    f"""CREATE TABLE IF NOT EXISTS audit_events (
    event_id       TEXT PRIMARY KEY,
    timestamp      TEXT NOT NULL DEFAULT {_NOW},
    actor_id       TEXT NOT NULL,
    action         TEXT NOT NULL,
    case_id        TEXT REFERENCES cases(case_id),
    details_json   TEXT NOT NULL DEFAULT '{{}}',
    previous_hash  TEXT,
    current_hash   TEXT NOT NULL UNIQUE
)""",
    f"""CREATE TABLE IF NOT EXISTS referrals (
    referral_id    TEXT PRIMARY KEY,
    case_id        TEXT NOT NULL REFERENCES cases(case_id),
    from_facility  TEXT NOT NULL,
    to_facility    TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT {_NOW}
)""",
    "CREATE INDEX IF NOT EXISTS idx_consent_case ON consent(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_referrals_case ON referrals(case_id)",
)


def _append_only(table: str) -> tuple[str, str]:
    return (
        f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    )


# ── Step 2: privacy tables (Phase 3). Additive only: legacy `consent`/`audit_events` are kept
# (deprecated, unused) so no existing data or test is affected. ──────────────────────────────────
PRIVACY_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE cases ADD COLUMN created_by TEXT",
    """CREATE TABLE consent_events (
    seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL UNIQUE,
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    purpose               TEXT NOT NULL CHECK (purpose IN ('triage', 'ai_assist')),
    action                TEXT NOT NULL CHECK (action IN ('granted', 'declined', 'withdrawn')),
    notice_version        TEXT NOT NULL,
    language              TEXT NOT NULL,
    notice_review_status  TEXT NOT NULL,
    method                TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    created_at            TEXT NOT NULL
)""",
    """CREATE TABLE triage_runs (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL UNIQUE,
    case_id          TEXT NOT NULL REFERENCES cases(case_id),
    consent_seq      INTEGER NOT NULL REFERENCES consent_events(seq),
    urgency          TEXT NOT NULL,
    result_json      TEXT NOT NULL,
    engine_version   TEXT NOT NULL,
    ruleset_version  TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL
)""",
    """CREATE TABLE audit_log (
    seq            INTEGER PRIMARY KEY,
    event_id       TEXT NOT NULL UNIQUE,
    timestamp      TEXT NOT NULL,
    actor_id       TEXT NOT NULL,
    actor_role     TEXT NOT NULL,
    action         TEXT NOT NULL,
    case_id        TEXT,
    outcome        TEXT NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
    request_id     TEXT,
    details_json   TEXT NOT NULL,
    previous_hash  TEXT NOT NULL,
    current_hash   TEXT NOT NULL UNIQUE
)""",
    "CREATE INDEX idx_consent_events_case ON consent_events(case_id, purpose, seq)",
    "CREATE INDEX idx_triage_runs_case ON triage_runs(case_id)",
    "CREATE INDEX idx_audit_log_case ON audit_log(case_id)",
    *_append_only("consent_events"),
    *_append_only("triage_runs"),
    *_append_only("audit_log"),
)

# ── Step 3: voice pipeline (Phase 4, docs/12). ──────────────────────────────────────────────────
# (a) consent_events gains the `voice_cloud` purpose. SQLite cannot alter a CHECK constraint, so the
#     table is rebuilt with the documented procedure (sqlite.org/lang_altertable.html §7): this step runs
#     with foreign_keys OFF (see FK_OFF_STEPS), copies every row with its original seq, drops the old
#     table, renames, recreates index and append-only triggers, and `run_migrations` then runs
#     `PRAGMA foreign_key_check` before COMMIT. triage_runs.consent_seq references resolve by name.
# (b) voice tables. No audio is stored anywhere; transcripts are untrusted machine output.
VOICE_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE consent_events_v3 (
    seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL UNIQUE,
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    purpose               TEXT NOT NULL CHECK (purpose IN ('triage', 'ai_assist', 'voice_cloud')),
    action                TEXT NOT NULL CHECK (action IN ('granted', 'declined', 'withdrawn')),
    notice_version        TEXT NOT NULL,
    language              TEXT NOT NULL,
    notice_review_status  TEXT NOT NULL,
    method                TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    created_at            TEXT NOT NULL
)""",
    "INSERT INTO consent_events_v3 (seq, event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
    "SELECT seq, event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at FROM consent_events ORDER BY seq",
    "DROP TABLE consent_events",  # its index and triggers are dropped with it (DROP fires no triggers)
    "ALTER TABLE consent_events_v3 RENAME TO consent_events",
    "CREATE INDEX idx_consent_events_case ON consent_events(case_id, purpose, seq)",
    *_append_only("consent_events"),
    """CREATE TABLE voice_transcriptions (
    transcription_id  TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    created_by        TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL,
    audio_sha256      TEXT NOT NULL,
    language          TEXT NOT NULL CHECK (language IN ('en', 'hi', 'or')),
    engine            TEXT NOT NULL CHECK (engine IN ('local', 'cloud')),
    status            TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'no_speech', 'empty_transcript', 'failed')),
    model_id          TEXT,
    mode              TEXT,
    audio_duration_ms INTEGER NOT NULL,
    segments_json     TEXT NOT NULL DEFAULT '[]',
    transcript_raw    TEXT,
    failure_code      TEXT,
    consent_seq       INTEGER NOT NULL REFERENCES consent_events(seq),
    created_at        TEXT NOT NULL,
    completed_at      TEXT,
    UNIQUE (case_id, idempotency_key)
)""",
    # A transcription row is written as `pending` before any engine runs (idempotency) and may be
    # finalised exactly once; after that it is immutable. It is never deleted.
    "CREATE TRIGGER voice_transcriptions_final BEFORE UPDATE ON voice_transcriptions WHEN OLD.status != 'pending' "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER voice_transcriptions_no_delete BEFORE DELETE ON voice_transcriptions BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    """CREATE TABLE voice_candidates (
    candidate_id      TEXT PRIMARY KEY,
    transcription_id  TEXT NOT NULL REFERENCES voice_transcriptions(transcription_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    ordinal           INTEGER NOT NULL,
    field             TEXT NOT NULL,
    char_start        INTEGER NOT NULL,
    char_end          INTEGER NOT NULL,
    raw_value         REAL,
    raw_value2        REAL,
    unit              TEXT,
    normalized_json   TEXT,
    flags_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    """CREATE TABLE voice_readback_events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL UNIQUE,
    candidate_id      TEXT NOT NULL REFERENCES voice_candidates(candidate_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    outcome           TEXT NOT NULL CHECK (outcome IN ('confirmed', 'corrected', 'rejected', 'unsure')),
    resolved_field    TEXT,
    resolved_json     TEXT,
    resolved_unit     TEXT,
    actor_id          TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    "CREATE INDEX idx_voice_transcriptions_case ON voice_transcriptions(case_id)",
    "CREATE INDEX idx_voice_candidates_transcription ON voice_candidates(transcription_id, ordinal)",
    "CREATE INDEX idx_voice_readback_candidate ON voice_readback_events(candidate_id, seq)",
    *_append_only("voice_candidates"),
    *_append_only("voice_readback_events"),
)

MIGRATIONS: tuple[tuple[str, ...], ...] = (BASELINE_STATEMENTS, PRIVACY_STATEMENTS, VOICE_STATEMENTS)
SCHEMA_VERSION = len(MIGRATIONS)
# Steps that rebuild a referenced table: foreign-key enforcement is switched off around the step (the
# pragma is a no-op inside a transaction), and integrity is re-checked with foreign_key_check before
# COMMIT, so a step that leaves a dangling reference still rolls back.
FK_OFF_STEPS = frozenset({3})

TABLES = ("cases", "consent", "triage_notes", "audit_events", "referrals")
PRIVACY_TABLES = ("consent_events", "triage_runs", "audit_log")
VOICE_TABLES = ("voice_transcriptions", "voice_candidates", "voice_readback_events")


class MigrationError(RuntimeError):
    def __init__(self, step: int):
        super().__init__(f"database migration step {step} failed and was rolled back")
        self.step = step


async def _connect(path: Path, timeout: float = 5.0) -> aiosqlite.Connection:
    # isolation_level must be passed to connect(): setting it afterwards fails across threads.
    conn = await aiosqlite.connect(path, isolation_level=None, timeout=timeout)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")  # no effect inside a transaction, so set it first
    return conn


@asynccontextmanager
async def transaction(conn: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """Write transaction: BEGIN IMMEDIATE takes the write lock up front; any exception rolls back."""
    await conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        await conn.execute("ROLLBACK")
        raise
    await conn.execute("COMMIT")


@asynccontextmanager
async def read_transaction(conn: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """Consistent snapshot for multi-statement reads; rolls back (never commits) on error."""
    await conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        await conn.execute("ROLLBACK")
        raise
    await conn.execute("COMMIT")


async def _user_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def run_migrations(conn: aiosqlite.Connection, migrations: tuple[tuple[str, ...], ...] = MIGRATIONS, fk_off_steps: frozenset[int] = FK_OFF_STEPS) -> int:
    """Apply pending steps; each step (DDL + FK check + version bump) is one atomic transaction."""
    for step, statements in enumerate(migrations, start=1):
        fk_off = step in fk_off_steps and await _user_version(conn) < step
        if fk_off:
            await conn.execute("PRAGMA foreign_keys = OFF")
        try:
            async with transaction(conn):
                if await _user_version(conn) >= step:  # re-read under the lock: another process may have applied it
                    continue
                for statement in statements:
                    await conn.execute(statement)
                async with conn.execute("PRAGMA foreign_key_check") as cur:
                    if await cur.fetchone() is not None:
                        raise MigrationError(step)
                await conn.execute(f"PRAGMA user_version = {int(step)}")
        except MigrationError:
            raise
        except Exception as exc:
            raise MigrationError(step) from exc
        finally:
            if fk_off:
                await conn.execute("PRAGMA foreign_keys = ON")
    return await _user_version(conn)


async def init_db(path: Path | None = None) -> None:
    path = path or get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await _connect(path)
    try:
        await run_migrations(conn)
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
