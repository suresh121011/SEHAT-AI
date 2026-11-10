"""Migration step-13 collision (remediation plan §3C). Disposable databases only; sehat.db is never touched.

The unmerged contribution 21a9761 defined its own "step 13" (gemini_live_*), so a database it migrated sits at
user_version 13 without main's step-13 tables (encounter summary). Version-only migration would skip main's step 13
for ever, silently. These tests build such databases from main's first 12 steps plus the contribution's DDL (copied
here: the test cannot import the contributor branch) and prove the forward repair: every expected table appears, every
existing row is kept, append-only still holds, foreign keys stay clean, and an ordinary database is left alone.
"""

import asyncio
import logging
import sqlite3

import aiosqlite
import pytest

from app.database import ENCOUNTER_SUMMARY_STATEMENTS, MIGRATIONS, PATIENT_REGISTRY_STATEMENTS, SCHEMA_VERSION, SchemaMismatchError, run_migrations

# The contribution's step-13 DDL, copied from 21a9761 backend/app/database.py (GEMINI_LIVE_STATEMENTS).
CONTRIBUTOR_STEP_13 = (
    """CREATE TABLE gemini_live_sessions (
    session_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id), language TEXT NOT NULL CHECK (language IN ('hi', 'or')),
    actor_id TEXT NOT NULL, actor_role TEXT NOT NULL, consent_seq INTEGER NOT NULL REFERENCES consent_events(seq),
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'interrupted', 'failed')), finish_reason TEXT, created_at TEXT NOT NULL, completed_at TEXT)""",
    """CREATE TABLE gemini_live_fields (
    field_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES gemini_live_sessions(session_id), case_id TEXT NOT NULL REFERENCES cases(case_id),
    field_name TEXT NOT NULL, value TEXT NOT NULL, unit TEXT, patient_phrase TEXT, source TEXT NOT NULL DEFAULT 'gemini_live_duplex',
    status TEXT NOT NULL CHECK (status IN ('pending_review', 'accepted', 'corrected', 'rejected')), created_at TEXT NOT NULL)""",
    "CREATE INDEX idx_gemini_live_case ON gemini_live_sessions(case_id)",
    "CREATE INDEX idx_gemini_live_fields_case ON gemini_live_fields(case_id)",
)
SUMMARY_TABLES = {"encounter_summaries", "encounter_summary_review_events"}
REGISTRY_TABLES = {"patients", "patient_link_events"}


async def _open(path):
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _seed(path, cases=2):
    """Synthetic rows: cases, consent events and one contributor session per case."""
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        for i in range(cases):
            cid = f"case-{i}"
            db.execute("INSERT INTO cases (case_id, patient_token, facility_code, scenario, status) VALUES (?, ?, 'PHC-KHURDA-01', 'opd', 'open')", (cid, f"tok-{i}"))
            for purpose in ("triage", "voice_cloud"):
                db.execute("INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
                           "VALUES (?, ?, ?, 'granted', 'v', 'hi', 'draft_unreviewed', 'staff_attested_verbal', 'anm', 'anm', 't')", (f"ev-{i}-{purpose}", cid, purpose))
            if _tables(path) >= {"gemini_live_sessions"}:
                seq = db.execute("SELECT MAX(seq) FROM consent_events WHERE case_id = ?", (cid,)).fetchone()[0]
                db.execute("INSERT INTO gemini_live_sessions VALUES (?, ?, 'hi', 'anm', 'anm', ?, 'completed', 'patient_finished', 't', 't')", (f"s-{i}", cid, seq))


def _tables(path):
    with sqlite3.connect(path) as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _version(path):
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def _contributor_db(tmp_path, then_main_step_14=False):
    """What a teammate's machine holds after running 21a9761: main's steps 1–12, the contribution's step 13."""
    path = tmp_path / "contributor.db"

    async def build():
        conn = await _open(path)
        try:
            assert await run_migrations(conn, MIGRATIONS[:12]) == 12
            for st in CONTRIBUTOR_STEP_13:
                await conn.execute(st)
            await conn.execute("PRAGMA user_version = 13")
            await conn.commit()
        finally:
            await conn.close()
    asyncio.run(build())
    _seed(path)
    if then_main_step_14:  # pulled main before this fix: step 13 skipped by number, step 14 ran
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA foreign_keys = OFF")
            for st in PATIENT_REGISTRY_STATEMENTS:
                db.execute(st)
            db.execute("PRAGMA user_version = 14")
        assert not (_tables(path) & SUMMARY_TABLES)  # the silent breakage this fix repairs
    return path


def _migrate(path, migrations=MIGRATIONS):
    async def go():
        conn = await _open(path)
        try:
            return await run_migrations(conn, migrations)
        finally:
            await conn.close()
    return asyncio.run(go())


def _snapshot(path):
    with sqlite3.connect(path) as db:
        return {
            "cases": db.execute("SELECT case_id, patient_token FROM cases ORDER BY case_id").fetchall(),
            "consent": db.execute("SELECT seq, event_id, case_id, purpose, action FROM consent_events ORDER BY seq").fetchall(),
            "gemini": db.execute("SELECT session_id, case_id, status FROM gemini_live_sessions ORDER BY session_id").fetchall()
            if "gemini_live_sessions" in _tables(path) else [],
        }


@pytest.mark.parametrize("then_main_step_14", [False, True], ids=["contributor_v13", "contributor_then_main_v14"])
def test_a_database_from_the_contribution_is_repaired_forward_with_every_row_kept(tmp_path, caplog, then_main_step_14):
    path = _contributor_db(tmp_path, then_main_step_14)
    before = _snapshot(path)
    with caplog.at_level(logging.WARNING, logger="sehat.database"):
        assert _migrate(path) == SCHEMA_VERSION == 17
    assert SUMMARY_TABLES | REGISTRY_TABLES <= _tables(path)
    after = _snapshot(path)
    assert after["cases"] == before["cases"] and after["gemini"] == before["gemini"]  # nothing lost, leftovers untouched
    assert [r[:5] for r in after["consent"]] == [r[:5] for r in before["consent"]]  # step 14's rebuild keeps every event
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        seq = db.execute("SELECT MAX(seq) FROM consent_events WHERE case_id = 'case-0'").fetchone()[0]
        db.execute("INSERT INTO encounter_summaries (summary_id, case_id, inventory_sha256, schema_version, status, summary_json, consent_seq, actor_id, actor_role, created_at) "
                   "VALUES ('sum-0', 'case-0', 'x', 'v', 'complete', '{}', ?, 'mo', 'medical_officer', 't')", (seq,))
        db.execute("INSERT INTO encounter_summary_review_events (event_id, summary_id, case_id, outcome, acknowledged_conflicts, actor_id, actor_role, created_at) "
                   "VALUES ('rev-0', 'sum-0', 'case-0', 'needs_changes', 0, 'mo', 'medical_officer', 't')")
        for sql in ("DELETE FROM encounter_summaries", "UPDATE encounter_summary_review_events SET outcome = 'accepted'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(sql)
    msgs = [r.getMessage() for r in caplog.records if r.name == "sehat.database"]
    assert any("schema_repaired step=13" in m and "leftover_gemini_tables=True" in m for m in msgs)
    assert not any("case-" in m or "tok-" in m for m in msgs)  # the log carries no data


def test_repair_is_idempotent(tmp_path):
    path = _contributor_db(tmp_path)
    assert _migrate(path) == SCHEMA_VERSION
    snap = _snapshot(path)
    assert _migrate(path) == SCHEMA_VERSION and _snapshot(path) == snap


def test_ordinary_databases_are_never_repaired(tmp_path, caplog):
    fresh, main13 = tmp_path / "fresh.db", tmp_path / "main13.db"
    assert _migrate(main13, MIGRATIONS[:13]) == 13
    _seed(main13)
    with caplog.at_level(logging.WARNING, logger="sehat.database"):
        assert _migrate(fresh) == SCHEMA_VERSION
        assert _migrate(main13) == SCHEMA_VERSION
    assert not [r for r in caplog.records if "schema_repaired" in r.getMessage()]
    assert SUMMARY_TABLES | REGISTRY_TABLES <= _tables(fresh) and "gemini_live_sessions" not in _tables(fresh)


def test_partial_migration_lists_never_trigger_the_repair(tmp_path):
    path = _contributor_db(tmp_path)
    with pytest.raises(SchemaMismatchError, match="newer"):  # an older code line refuses it (M-3) and changes nothing
        _migrate(path, MIGRATIONS[:12])
    assert not (_tables(path) & SUMMARY_TABLES) and _version(path) == 13


def test_the_repair_applies_exactly_main_step_13(tmp_path):
    path = _contributor_db(tmp_path)
    _migrate(path)
    with sqlite3.connect(path) as db:
        triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name LIKE 'encounter_summar%'")}
    expected = {s.split()[2] for s in ENCOUNTER_SUMMARY_STATEMENTS if s.startswith("CREATE TRIGGER")}
    assert expected and expected <= triggers
