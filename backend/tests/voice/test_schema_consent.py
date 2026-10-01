"""Migration 3 (consent_events rebuild + voice tables) and the `voice_cloud` consent purpose."""

import asyncio
import sqlite3

import pytest

from app.database import SCHEMA_VERSION, MIGRATIONS, VOICE_TABLES, _connect, run_migrations
from tests.privacy.helpers import audit_rows, auth, new_case, token_for, triage, withdraw
from tests.privacy.helpers import grant as grant_consent


async def _migrate(path, migrations=MIGRATIONS):
    conn = await _connect(path)
    try:
        return await run_migrations(conn, migrations)
    finally:
        await conn.close()


def _populated_v2(tmp_path):
    db = tmp_path / "v2.db"
    asyncio.run(_migrate(db, MIGRATIONS[:2]))
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("INSERT INTO cases (case_id, patient_token, facility_code, scenario, status) VALUES ('c1','t','PHC-1','opd','open')")
    for i, purpose in enumerate(("triage", "ai_assist", "triage")):
        c.execute(
            "INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
            f"VALUES ('e{i}','c1','{purpose}','granted','v','en','r','patient_button','a','patient','t')"
        )
    c.execute("INSERT INTO triage_runs (run_id, case_id, consent_seq, urgency, result_json, engine_version, ruleset_version, actor_id, created_at) VALUES ('r1','c1',3,'RED','{}','1','1','a','t')")
    c.close()
    return db


def test_v2_database_with_data_upgrades_preserving_seq_and_references(tmp_path):
    db = _populated_v2(tmp_path)
    assert asyncio.run(_migrate(db)) == SCHEMA_VERSION  # v2 data migrates through every later step
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA foreign_keys = ON")
    assert c.execute("SELECT seq, event_id, purpose FROM consent_events ORDER BY seq").fetchall() == [(1, "e0", "triage"), (2, "e1", "ai_assist"), (3, "e2", "triage")]
    assert c.execute("SELECT consent_seq FROM triage_runs").fetchone() == (3,)
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert set(VOICE_TABLES) <= {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "consent_events_v3" not in {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    # New purpose accepted, seq continues after the copied rows, foreign keys and triggers still enforced.
    c.execute(
        "INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
        "VALUES ('e9','c1','voice_cloud','granted','v','en','r','patient_button','a','patient','t')"
    )
    assert c.execute("SELECT max(seq) FROM consent_events").fetchone() == (4,)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        c.execute("INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) VALUES ('ex','c1','other','granted','v','en','r','m','a','p','t')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO triage_runs (run_id, case_id, consent_seq, urgency, result_json, engine_version, ruleset_version, actor_id, created_at) VALUES ('r2','c1',99,'RED','{}','1','1','a','t')")
    for stmt in ("UPDATE consent_events SET action = 'declined'", "DELETE FROM consent_events"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute(stmt)
    assert c.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_dangling_reference_makes_the_rebuild_roll_back(tmp_path):
    db = _populated_v2(tmp_path)
    broken = MIGRATIONS[:2] + (MIGRATIONS[2][:2] + ("DELETE FROM consent_events_v3 WHERE seq = 3",) + MIGRATIONS[2][2:],)
    from app.database import MigrationError

    with pytest.raises(MigrationError):
        asyncio.run(_migrate(db, broken))
    c = sqlite3.connect(db)
    assert c.execute("PRAGMA user_version").fetchone() == (2,)
    assert c.execute("SELECT count(*) FROM consent_events").fetchone() == (3,)  # untouched


def test_voice_transcription_is_final_once_completed(tmp_path):
    db = tmp_path / "v.db"
    asyncio.run(_migrate(db))
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("INSERT INTO cases (case_id, patient_token, facility_code, scenario, status) VALUES ('c1','t','PHC-1','opd','open')")
    c.execute("INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) VALUES ('e','c1','triage','granted','v','en','r','m','a','anm','t')")
    c.execute(
        "INSERT INTO voice_transcriptions (transcription_id, case_id, created_by, idempotency_key, audio_sha256, language, engine, status, audio_duration_ms, consent_seq, created_at) "
        "VALUES ('t1','c1','u','k','h','en','local','pending',1000,1,'t')"
    )
    c.execute("UPDATE voice_transcriptions SET status='completed', transcript_raw='x' WHERE transcription_id='t1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE voice_transcriptions SET transcript_raw='changed' WHERE transcription_id='t1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("DELETE FROM voice_transcriptions")


# ── voice_cloud consent semantics ────────────────────────────────────────────────────────────────


@pytest.fixture
def anm(client):
    return token_for(client, "anm")


def _state(client, token, cid):
    return client.get(f"/api/v1/cases/{cid}", headers=auth(token)).json()["consent"]


def test_voice_cloud_is_opt_in_and_omission_declines(client, anm):
    cid = new_case(client, anm)
    grant_consent(client, anm, cid)
    assert _state(client, anm, cid)["voice_cloud"] == "declined"
    cid2 = new_case(client, anm)
    grant_consent(client, anm, cid2, voice_cloud=True)
    assert _state(client, anm, cid2) == {"triage": "granted", "ai_assist": "declined", "voice_cloud": "granted"}


def test_decline_with_voice_cloud_is_rejected(client, anm):
    from app.consent_notice import NOTICE_VERSION

    cid = new_case(client, anm)
    body = {"decision": "decline", "include_voice_cloud": True, "language": "en", "notice_version": NOTICE_VERSION}
    assert client.post(f"/api/v1/cases/{cid}/consent", json=body, headers=auth(anm)).status_code == 400


def test_withdrawing_voice_cloud_keeps_triage(client, anm):
    cid = new_case(client, anm)
    grant_consent(client, anm, cid, voice_cloud=True)
    assert withdraw(client, anm, cid, "voice_cloud").json()["consent"] == {"triage": "granted", "ai_assist": "declined", "voice_cloud": "withdrawn"}
    assert triage(client, anm, cid).status_code == 200


def test_withdrawing_triage_cascades_to_voice_cloud_and_ai_assist(client, anm):
    cid = new_case(client, anm)
    grant_consent(client, anm, cid, ai=True, voice_cloud=True)
    resp = withdraw(client, anm, cid, "triage")
    assert resp.json()["consent"] == {"triage": "withdrawn", "ai_assist": "withdrawn", "voice_cloud": "withdrawn"}
    hist = client.get(f"/api/v1/cases/{cid}/consent", headers=auth(anm)).json()["history"]
    assert [(h["purpose"], h["method"]) for h in hist[-3:]] == [
        ("triage", "staff_attested_verbal"),
        ("ai_assist", "cascade_from_triage"),
        ("voice_cloud", "cascade_from_triage"),
    ]
    last = [r for r in audit_rows(client) if r["case_id"] == cid][-1]
    assert last["action"] == "consent_withdrawn" and "voice_cloud" in last["details_json"]


def test_notice_mentions_sarvam_retention_plainly(client, anm):
    for lang in ("en", "hi", "or"):
        body = client.get(f"/api/v1/consent/notice?language={lang}", headers=auth(anm)).json()
        text = " ".join(body["paragraphs"]) + body["purposes"]["voice_cloud"]
        assert "Sarvam" in text and "30" in text
    en = client.get("/api/v1/consent/notice?language=en", headers=auth(anm)).json()
    assert "No voice recording is stored" not in " ".join(en["paragraphs"])
    assert any("does not store the voice recording" in p for p in en["paragraphs"])
    assert "train its models" in en["purposes"]["voice_cloud"] and "read back" in en["purposes"]["voice_cloud"]  # wording per Sarvam privacy policy (2026-10-01)
