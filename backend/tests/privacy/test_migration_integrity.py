"""Schema integrity after migration (final remediation plan M-1, M-2, M-3). Disposable databases only; sehat.db is never
touched. `user_version` alone is not trusted: a database from newer code is refused before any change, and after
migrating, every table, column, index and trigger the migrations define must exist as defined. Extra leftover objects
(for example the contribution's unused gemini_live_* tables) are allowed.
"""

import asyncio
import hashlib
import sqlite3

import aiosqlite
import pytest

from app.database import MIGRATIONS, SCHEMA_VERSION, SchemaMismatchError, run_migrations


async def _open(path):
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(path, migrations=MIGRATIONS):
    async def go():
        conn = await _open(path)
        try:
            return await run_migrations(conn, migrations)
        finally:
            await conn.close()
    return asyncio.run(go())


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fresh(tmp_path, name="db.sqlite"):
    path = tmp_path / name
    assert _migrate(path) == SCHEMA_VERSION
    return path


def test_a_fresh_database_validates_and_a_second_run_is_a_no_op(tmp_path):
    path = _fresh(tmp_path)
    assert _migrate(path) == SCHEMA_VERSION


# ── M-3: newer code's database ────────────────────────────────────────────────────────────────────────────────

def test_a_database_from_newer_code_is_refused_and_left_untouched(tmp_path):
    path = _fresh(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 85}")
    before = _sha(path)
    with pytest.raises(SchemaMismatchError, match="newer"):
        _migrate(path)
    assert _sha(path) == before


def test_a_newer_database_without_summary_tables_is_not_repaired(tmp_path):
    path = _fresh(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE encounter_summary_review_events")
        db.execute("DROP TABLE encounter_summaries")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaMismatchError):
        _migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name = 'encounter_summaries'").fetchone() is None


# ── M-1 / M-2: schema validated, not inferred from the version number ─────────────────────────────────────────

def test_a_wrong_schema_summary_table_is_refused(tmp_path):
    """The step-13 repair used to accept any table called encounter_summaries."""
    path = _fresh(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE encounter_summary_review_events")
        db.execute("DROP TABLE encounter_summaries")
        db.execute("CREATE TABLE encounter_summaries (summary_id TEXT PRIMARY KEY)")
    with pytest.raises(SchemaMismatchError, match="encounter_summar"):
        _migrate(path)


def test_a_missing_append_only_trigger_is_refused(tmp_path):
    path = _fresh(tmp_path)
    with sqlite3.connect(path) as db:
        trig = db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'triage_runs'").fetchone()[0]
        db.execute(f"DROP TRIGGER {trig}")
    with pytest.raises(SchemaMismatchError, match=trig):
        _migrate(path)


def test_a_missing_index_is_refused(tmp_path):
    path = _fresh(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP INDEX idx_triage_runs_case")
    with pytest.raises(SchemaMismatchError, match="idx_triage_runs_case"):
        _migrate(path)


def test_version_14_without_step_14_schema_is_refused(tmp_path):
    """M-2: user_version 14 claimed, the patient registry never created."""
    path = tmp_path / "v13.sqlite"
    assert _migrate(path, MIGRATIONS[:13]) == 13
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    with pytest.raises(SchemaMismatchError, match="patient"):
        _migrate(path)


def test_extra_leftover_tables_are_allowed(tmp_path):
    path = _fresh(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE gemini_live_sessions (session_id TEXT PRIMARY KEY)")
    assert _migrate(path) == SCHEMA_VERSION


def test_the_error_names_objects_only_never_row_data(tmp_path):
    path = _fresh(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP INDEX idx_triage_runs_case")
    with pytest.raises(SchemaMismatchError) as exc:
        _migrate(path)
    msg = str(exc.value)
    assert "rebuild" in msg.lower() and str(path) not in msg
