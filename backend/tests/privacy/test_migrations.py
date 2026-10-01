"""Schema migrations: atomic per step, non-destructive, concurrency-safe (docs/11 §C)."""

import asyncio
import sqlite3

import pytest

from app.database import (
    BASELINE_STATEMENTS,
    MIGRATIONS,
    PRIVACY_TABLES,
    SCHEMA_VERSION,
    TABLES,
    MigrationError,
    _connect,
    run_migrations,
)


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _version(path) -> int:
    with sqlite3.connect(path) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


async def _migrate(path, migrations=MIGRATIONS) -> int:
    conn = await _connect(path)
    try:
        return await run_migrations(conn, migrations)
    finally:
        await conn.close()


def test_fresh_database_reaches_current_version(tmp_path):
    db = tmp_path / "fresh.db"
    assert asyncio.run(_migrate(db)) == SCHEMA_VERSION == 5
    assert set(TABLES) | set(PRIVACY_TABLES) <= _tables(db)


def test_legacy_v0_file_with_baseline_tables_migrates_without_touching_them(tmp_path):
    # Same shape as the repo's dev sehat.db: user_version 0, baseline tables present.
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as c:
        for stmt in BASELINE_STATEMENTS:
            c.execute(stmt)
        c.execute("INSERT INTO cases (case_id, patient_token, facility_code, scenario, status) VALUES ('c1','t1','PHC-1','opd','open')")
    assert _version(db) == 0
    asyncio.run(_migrate(db))
    assert _version(db) == 5
    assert set(TABLES) <= _tables(db)  # legacy tables kept (non-destructive)
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT case_id, created_by FROM cases").fetchone()
    assert row == ("c1", None)


def test_rerun_is_a_noop(tmp_path):
    db = tmp_path / "again.db"
    asyncio.run(_migrate(db))
    asyncio.run(_migrate(db))
    assert _version(db) == 5


def test_failed_step_rolls_back_completely(tmp_path):
    db = tmp_path / "broken.db"
    broken = (MIGRATIONS[0], MIGRATIONS[1][:3] + ("CREATE TABLE this is not sql",))
    with pytest.raises(MigrationError) as exc:
        asyncio.run(_migrate(db, broken))
    assert exc.value.step == 2
    assert _version(db) == 1  # step 1 committed, step 2 fully reverted
    assert not set(PRIVACY_TABLES) & _tables(db)
    with sqlite3.connect(db) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(cases)")]
    assert "created_by" not in cols  # the ALTER inside the failed step was rolled back too
    # recovery: fix the cause and rerun
    asyncio.run(_migrate(db))
    assert _version(db) == 5


def test_concurrent_runners_do_not_double_apply(tmp_path):
    db = tmp_path / "race.db"

    async def both():
        return await asyncio.gather(_migrate(db), _migrate(db))

    assert asyncio.run(both()) == [5, 5]
    assert _version(db) == 5


def _migrated(tmp_path):
    db = tmp_path / "m.db"
    asyncio.run(_migrate(db))
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA foreign_keys = ON")
    return c


def test_foreign_keys_enforced(tmp_path):
    c = _migrated(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
            "VALUES ('e1','missing-case','triage','granted','v','en','reviewed_draft','patient_button','a','patient','t')"
        )


@pytest.mark.parametrize("table", PRIVACY_TABLES)
def test_append_only_triggers_block_update_and_delete(tmp_path, table):
    c = _migrated(tmp_path)
    c.execute("INSERT INTO cases (case_id, patient_token, facility_code, scenario, status) VALUES ('c1','t','PHC-1','opd','open')")
    rows = {
        "consent_events": "INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) VALUES ('e1','c1','triage','granted','v','en','r','patient_button','a','patient','t')",
        "triage_runs": None,
        "audit_log": "INSERT INTO audit_log (seq, event_id, timestamp, actor_id, actor_role, action, outcome, details_json, previous_hash, current_hash) VALUES (1,'e1','t','a','anm','case_created','success','{}','0','h1')",
    }
    if table == "triage_runs":
        c.execute(rows["consent_events"])
        c.execute("INSERT INTO triage_runs (run_id, case_id, consent_seq, urgency, result_json, engine_version, ruleset_version, actor_id, created_at) VALUES ('r1','c1',1,'RED','{}','1','1','a','t')")
    else:
        c.execute(rows[table])
    for stmt in (f"UPDATE {table} SET seq = seq", f"DELETE FROM {table}"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute(stmt)
