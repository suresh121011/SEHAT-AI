"""Audit log: append-only, hash-chained, supervisor-only reads (docs/11 §H)."""

import sqlite3

import pytest

from app.config import get_settings
from tests.privacy.helpers import (
    audit_rows,
    auth,
    grant,
    new_case,
    token_for,
    triage,
    withdraw,
)

SYNTHETIC_PHONE = "9876543210"


@pytest.fixture
def populated(client):
    anm = token_for(client, "anm")
    cid = new_case(client, anm)
    grant(client, anm, cid, ai=True)
    triage(client, anm, cid)
    withdraw(client, anm, cid, "triage")
    return cid


def _verify(client):
    return client.post("/api/v1/audit/verify", headers=auth(token_for(client, "supervisor")))


def _admin_edit(sql: str, params=()):
    """Simulate a database-file administrator: drop the append-only triggers, then edit."""
    with sqlite3.connect(get_settings().database_path, isolation_level=None) as c:
        c.execute("DROP TRIGGER audit_log_no_update")
        c.execute("DROP TRIGGER audit_log_no_delete")
        c.execute(sql, params)


def test_events_capture_actor_from_token_and_no_raw_content(client, populated):
    rows = [r for r in audit_rows(client) if r["case_id"] == populated]
    assert [r["action"] for r in rows] == ["case_created", "consent_granted", "triage_recorded", "consent_withdrawn"]
    assert {r["actor_role"] for r in rows} == {"anm"}
    for r in rows:
        assert r["timestamp"] and r["current_hash"] and len(r["current_hash"]) == 64
        assert SYNTHETIC_PHONE not in r["details_json"]


def test_chain_verifies_and_verify_does_not_cover_itself(client, populated):
    before = len(audit_rows(client))
    body = _verify(client).json()
    assert body == {"ok": True, "verified_through_seq": before, "first_bad_seq": None}
    rows = audit_rows(client)
    assert rows[-1]["action"] == "audit_verified" and len(rows) == before + 1
    assert _verify(client).json()["ok"] is True  # the verify event itself chains correctly


def test_update_and_delete_are_rejected_by_triggers(client, populated):
    with sqlite3.connect(get_settings().database_path) as c:
        for stmt in ("UPDATE audit_log SET outcome='failure'", "DELETE FROM audit_log"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(stmt)


@pytest.mark.parametrize(
    "tamper,expected_first_bad",
    [
        ("UPDATE audit_log SET details_json='{}' WHERE seq=2", 2),  # modified row
        ("DELETE FROM audit_log WHERE seq=2", 3),  # middle deletion → seq gap
        ("UPDATE audit_log SET previous_hash='deadbeef' WHERE seq=3", 3),  # broken link
        ("UPDATE audit_log SET current_hash='00' WHERE seq=1", 1),  # altered hash
    ],
)
def test_tampering_is_detected(client, populated, tamper, expected_first_bad):
    _admin_edit(tamper)
    body = _verify(client).json()
    assert body["ok"] is False and body["first_bad_seq"] == expected_first_bad
    assert "details" not in body  # verification never returns row contents


def test_tail_truncation_is_not_detected_known_limitation(client, populated):
    """Documents a known limit: without an external checkpoint, removing the newest rows is invisible."""
    last = audit_rows(client)[-1]["seq"]
    _admin_edit("DELETE FROM audit_log WHERE seq=?", (last,))
    assert _verify(client).json()["ok"] is True


@pytest.mark.parametrize("who", ["patient", "anm", "mo"])
def test_only_supervisor_reads_or_verifies_audit(client, populated, who):
    token = token_for(client, who)
    assert client.get(f"/api/v1/audit/{populated}", headers=auth(token)).status_code == 403
    assert client.post("/api/v1/audit/verify", headers=auth(token)).status_code == 403


def test_supervisor_case_view_omits_actor_id(client, populated):
    body = client.get(f"/api/v1/audit/{populated}", headers=auth(token_for(client, "supervisor"))).json()
    assert body["events"] and all("actor_id" not in e for e in body["events"])
    assert {e["actor_role"] for e in body["events"]} == {"anm"}


def test_verify_is_post_only(client):
    assert client.get("/api/v1/audit/verify", headers=auth(token_for(client, "supervisor"))).status_code in (400, 404, 405)


def test_non_uuid_request_id_is_not_stored(client):
    anm = token_for(client, "anm")
    client.post("/api/v1/cases", json={"scenario": "opd", "facility_code": "PHC-1"}, headers={**auth(anm), "X-Request-ID": f"{SYNTHETIC_PHONE} name"})
    assert all(SYNTHETIC_PHONE not in (r["request_id"] or "") for r in audit_rows(client))


def test_record_outside_transaction_is_refused(tmp_path):
    import asyncio

    from app import audit
    from app.auth import Principal, Role
    from app.database import _connect, run_migrations

    async def go():
        conn = await _connect(tmp_path / "a.db")
        await run_migrations(conn)
        try:
            await audit.record(conn, principal=Principal("u", "x", Role.ANM), action="case_created", outcome="success", details=audit.CaseCreatedDetails(scenario="opd", facility_code="X"))
        finally:
            await conn.close()

    with pytest.raises(RuntimeError, match="inside a write transaction"):
        asyncio.run(go())
