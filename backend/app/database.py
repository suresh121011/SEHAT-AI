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

# ── Step 4: OCR documents (Phase 5, docs/14). Additive only (new tables; nothing rebuilt). Page images
# live on the local filesystem (architecture: "local filesystem (documents)"); rows hold path + hash.
# Everything is append-only; a document row may be finalised exactly once. Deletion/retention is not
# implemented in Phase 5 (docs/14 §10), consistent with voice transcripts.
OCR_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE ocr_documents (
    document_id        TEXT PRIMARY KEY,
    case_id            TEXT NOT NULL REFERENCES cases(case_id),
    created_by         TEXT NOT NULL,
    idempotency_key    TEXT NOT NULL,
    upload_sha256      TEXT NOT NULL,
    media_type         TEXT NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg', 'application/pdf')),
    document_type      TEXT NOT NULL CHECK (document_type IN ('lab_report', 'prescription', 'discharge_summary')),
    byte_size          INTEGER NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'quality_rejected', 'no_text', 'failed')),
    failure_code       TEXT,
    page_count         INTEGER,
    quality_json       TEXT,
    engines_json       TEXT,
    pipeline_version   TEXT NOT NULL,
    overall_confidence REAL,
    dates_json         TEXT,
    consent_seq        INTEGER NOT NULL REFERENCES consent_events(seq),
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    UNIQUE (case_id, idempotency_key)
)""",
    "CREATE TRIGGER ocr_documents_final BEFORE UPDATE ON ocr_documents WHEN OLD.status != 'pending' "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER ocr_documents_no_delete BEFORE DELETE ON ocr_documents BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    """CREATE TABLE ocr_pages (
    document_id     TEXT NOT NULL REFERENCES ocr_documents(document_id),
    page_index      INTEGER NOT NULL,
    file_ref        TEXT NOT NULL,
    png_sha256      TEXT NOT NULL,
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    transform_json  TEXT NOT NULL,
    PRIMARY KEY (document_id, page_index)
)""",
    """CREATE TABLE ocr_fields (
    field_id              TEXT PRIMARY KEY,
    document_id           TEXT NOT NULL REFERENCES ocr_documents(document_id),
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    ordinal               INTEGER NOT NULL,
    kind                  TEXT NOT NULL CHECK (kind IN ('lab', 'medication')),
    page_index            INTEGER NOT NULL,
    payload_json          TEXT NOT NULL,
    regions_json          TEXT NOT NULL,
    readings_json         TEXT NOT NULL,
    checks_json           TEXT NOT NULL,
    band                  TEXT NOT NULL CHECK (band IN ('accept', 'amber', 'human_entry')),
    field_confidence      REAL,
    disputed              INTEGER NOT NULL,
    created_at            TEXT NOT NULL
)""",
    """CREATE TABLE ocr_attestation_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    document_id  TEXT NOT NULL REFERENCES ocr_documents(document_id),
    case_id      TEXT NOT NULL REFERENCES cases(case_id),
    answer       TEXT NOT NULL CHECK (answer IN ('matches', 'does_not_match', 'unsure')),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL
)""",
    """CREATE TABLE ocr_review_events (
    seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL UNIQUE,
    field_id              TEXT NOT NULL REFERENCES ocr_fields(field_id),
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    outcome               TEXT NOT NULL CHECK (outcome IN ('confirmed', 'corrected', 'rejected', 'unsure')),
    corrected_json        TEXT,
    shown_png_sha256      TEXT,
    shown_regions_sha256  TEXT,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    created_at            TEXT NOT NULL
)""",
    "CREATE INDEX idx_ocr_documents_case ON ocr_documents(case_id)",
    "CREATE INDEX idx_ocr_fields_document ON ocr_fields(document_id, ordinal)",
    "CREATE INDEX idx_ocr_attestation_document ON ocr_attestation_events(document_id, seq)",
    "CREATE INDEX idx_ocr_review_field ON ocr_review_events(field_id, seq)",
    *_append_only("ocr_pages"),
    *_append_only("ocr_fields"),
    *_append_only("ocr_attestation_events"),
    *_append_only("ocr_review_events"),
)

# ── Step 5: OCR document deletion and retention (Phase 5, docs/14 §3). A document's content may be deleted
# only after an append-only purge event is recorded for it (reviewer request or retention expiry). The purge
# event, the document's id row and the audit log remain as evidence that it existed and was deleted.
_PURGED = "EXISTS (SELECT 1 FROM ocr_purge_events p WHERE p.document_id = OLD.document_id)"
OCR_PURGE_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE ocr_purge_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    document_id  TEXT NOT NULL UNIQUE REFERENCES ocr_documents(document_id),
    case_id      TEXT NOT NULL REFERENCES cases(case_id),
    reason       TEXT NOT NULL CHECK (reason IN ('reviewer_request', 'retention_expired')),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL
)""",
    *_append_only("ocr_purge_events"),
    "DROP TRIGGER ocr_pages_no_delete",
    f"CREATE TRIGGER ocr_pages_no_delete BEFORE DELETE ON ocr_pages WHEN NOT {_PURGED} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "DROP TRIGGER ocr_fields_no_delete",
    f"CREATE TRIGGER ocr_fields_no_delete BEFORE DELETE ON ocr_fields WHEN NOT {_PURGED} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "DROP TRIGGER ocr_attestation_events_no_delete",
    f"CREATE TRIGGER ocr_attestation_events_no_delete BEFORE DELETE ON ocr_attestation_events WHEN NOT {_PURGED} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "DROP TRIGGER ocr_review_events_no_delete",
    "CREATE TRIGGER ocr_review_events_no_delete BEFORE DELETE ON ocr_review_events WHEN NOT EXISTS "
    "(SELECT 1 FROM ocr_purge_events p JOIN ocr_fields f ON f.document_id = p.document_id WHERE f.field_id = OLD.field_id) "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    # A finalised document row may change once more, only to clear its content columns after a purge event;
    # identity, type, status and hashes never change.
    "DROP TRIGGER ocr_documents_final",
    "CREATE TRIGGER ocr_documents_final BEFORE UPDATE ON ocr_documents WHEN OLD.status != 'pending' AND ("
    f"NOT {_PURGED} OR "
    + " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in (
        "document_id", "case_id", "created_by", "idempotency_key", "upload_sha256", "media_type", "document_type", "byte_size",
        "status", "failure_code", "page_count", "pipeline_version", "consent_seq", "created_at", "completed_at"))
    + " OR NEW.dates_json IS NOT NULL OR NEW.quality_json IS NOT NULL OR NEW.overall_confidence IS NOT NULL OR NEW.engines_json IS NOT NULL) "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
)

# ── Step 6: Phase 6 AI extraction (docs/16 §9). Append-only. Only redacted-derived content is stored: the
# redacted segments the provider saw, voted fields with their quotes, reviewer decisions and note drafts. Raw
# intake text is never stored. `triage_runs` is not touched by this step.
AI_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE ai_extraction_runs (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id     TEXT NOT NULL UNIQUE,
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    created_by        TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL,
    provider          TEXT NOT NULL,
    provider_kind     TEXT NOT NULL,
    model_id          TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    passes_requested  INTEGER NOT NULL,
    passes_valid      INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('completed', 'insufficient_agreement')),
    consent_seq       INTEGER NOT NULL REFERENCES consent_events(seq),
    segments_json     TEXT NOT NULL,
    skipped_json      TEXT NOT NULL,
    dropped_json      TEXT NOT NULL,
    abstentions_json  TEXT NOT NULL,
    urgency_json      TEXT NOT NULL,
    flags_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE (case_id, idempotency_key)
)""",
    """CREATE TABLE ai_fields (
    field_id          TEXT PRIMARY KEY,
    extraction_id     TEXT NOT NULL REFERENCES ai_extraction_runs(extraction_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    ordinal           INTEGER NOT NULL,
    origin            TEXT NOT NULL CHECK (origin IN ('model', 'ocr_reviewed')),
    field_key         TEXT NOT NULL,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('agreed', 'majority', 'disputed', 'disputed_raise', 'human_reviewed')),
    agreement         TEXT,
    value_json        TEXT NOT NULL,
    candidates_json   TEXT NOT NULL,
    evidence_json     TEXT NOT NULL,
    critical          INTEGER NOT NULL,
    priority_review   INTEGER NOT NULL,
    flags_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    """CREATE TABLE ai_field_review_events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL UNIQUE,
    field_id          TEXT NOT NULL REFERENCES ai_fields(field_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    outcome           TEXT NOT NULL CHECK (outcome IN ('accepted', 'corrected', 'rejected', 'unsure')),
    corrected_json    TEXT,
    supersedes        TEXT,
    actor_id          TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    """CREATE TABLE ai_note_drafts (
    seq                    INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id                TEXT NOT NULL UNIQUE,
    case_id                TEXT NOT NULL REFERENCES cases(case_id),
    extraction_id          TEXT NOT NULL REFERENCES ai_extraction_runs(extraction_id),
    triage_run_id          TEXT REFERENCES triage_runs(run_id),
    deterministic_urgency  TEXT,
    final_urgency          TEXT,
    blocked_count          INTEGER NOT NULL,
    note_json              TEXT NOT NULL,
    consent_seq            INTEGER NOT NULL REFERENCES consent_events(seq),
    actor_id               TEXT NOT NULL,
    actor_role             TEXT NOT NULL,
    created_at             TEXT NOT NULL
)""",
    *_append_only("ai_extraction_runs"),
    *_append_only("ai_fields"),
    *_append_only("ai_field_review_events"),
    *_append_only("ai_note_drafts"),
)

# ── Step 7: Phase 6 P1. New triage runs also store the validated rules-engine input (vitals, flags, age; no
# identifiers) so counterfactuals can be recomputed. Nullable: earlier runs have none. Rows stay append-only.
TRIAGE_INPUT_STATEMENTS: tuple[str, ...] = ("ALTER TABLE triage_runs ADD COLUMN input_json TEXT",)

MIGRATIONS: tuple[tuple[str, ...], ...] = (BASELINE_STATEMENTS, PRIVACY_STATEMENTS, VOICE_STATEMENTS, OCR_STATEMENTS, OCR_PURGE_STATEMENTS, AI_STATEMENTS,
                                           TRIAGE_INPUT_STATEMENTS)
SCHEMA_VERSION = len(MIGRATIONS)
# Steps that rebuild a referenced table: foreign-key enforcement is switched off around the step (the
# pragma is a no-op inside a transaction), and integrity is re-checked with foreign_key_check before
# COMMIT, so a step that leaves a dangling reference still rolls back.
FK_OFF_STEPS = frozenset({3})

TABLES = ("cases", "consent", "triage_notes", "audit_events", "referrals")
PRIVACY_TABLES = ("consent_events", "triage_runs", "audit_log")
VOICE_TABLES = ("voice_transcriptions", "voice_candidates", "voice_readback_events")
AI_TABLES = ("ai_extraction_runs", "ai_fields", "ai_field_review_events", "ai_note_drafts")
OCR_TABLES = ("ocr_documents", "ocr_pages", "ocr_fields", "ocr_attestation_events", "ocr_review_events", "ocr_purge_events")


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
