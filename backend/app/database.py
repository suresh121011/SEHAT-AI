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

MIGRATIONS: tuple[tuple[str, ...], ...] = (BASELINE_STATEMENTS, PRIVACY_STATEMENTS)
SCHEMA_VERSION = len(MIGRATIONS)

TABLES = ("cases", "consent", "triage_notes", "audit_events", "referrals")
PRIVACY_TABLES = ("consent_events", "triage_runs", "audit_log")


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


async def run_migrations(conn: aiosqlite.Connection, migrations: tuple[tuple[str, ...], ...] = MIGRATIONS) -> int:
    """Apply pending steps; each step (DDL + FK check + version bump) is one atomic transaction."""
    for step, statements in enumerate(migrations, start=1):
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
