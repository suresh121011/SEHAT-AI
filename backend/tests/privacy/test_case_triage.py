"""Case-bound triage under consent, incl. withdrawal race contracts D2/D3/D5 (docs/11 §D)."""

import asyncio
import sqlite3

import pytest

from app import case_triage, consent
from app.auth import Principal, Role, demo_user_id
from app.config import get_settings
from app.consent_notice import NOTICE_VERSION
from app.database import _connect, run_migrations
from app.rules import TriageInput
from tests.privacy.helpers import auth, grant, new_case, token_for, triage, withdraw
from tests.rules.vignettes import case as vignette


@pytest.fixture
def anm(client):
    return token_for(client, "anm")


def _runs(case_id):
    with sqlite3.connect(get_settings().database_path) as c:
        return c.execute("SELECT consent_seq, urgency FROM triage_runs WHERE case_id=? ORDER BY seq", (case_id,)).fetchall()


def test_granted_consent_allows_triage_and_records_run(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid)
    resp = triage(client, anm, cid, vitals={"resp_rate": 30})
    assert resp.status_code == 200
    assert resp.json()["result"]["urgency"] == "RED"
    assert _runs(cid) == [(1, "RED")]


def test_two_runs_keep_history_with_consent_reference(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid)
    triage(client, anm, cid)
    triage(client, anm, cid, vitals={"resp_rate": 30})
    assert [u for _, u in _runs(cid)] == ["GREEN", "RED"]
    assert all(seq == 1 for seq, _ in _runs(cid))


def test_no_consent_is_403_and_audited(client, anm):
    cid = new_case(client, anm)
    resp = triage(client, anm, cid)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert _runs(cid) == []


def test_d2_withdrawal_before_operation_denies(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid)
    withdraw(client, anm, cid, "triage")
    assert triage(client, anm, cid).status_code == 403
    assert _runs(cid) == []


def test_d5_withdrawal_after_completion_keeps_results_and_blocks_new(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid)
    triage(client, anm, cid)
    withdraw(client, anm, cid, "triage")
    assert triage(client, anm, cid).status_code == 403
    assert len(_runs(cid)) == 1  # earlier result retained (no deletion in Phase 3)


def test_scenario_mismatch_is_409(client, anm):
    cid = new_case(client, anm, scenario="opd")
    grant(client, anm, cid)
    resp = client.post(f"/api/v1/cases/{cid}/triage", json=vignette("health_camp"), headers=auth(anm))
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "SCENARIO_MISMATCH"


def test_triage_process_calculator_is_unaffected(client):
    mo = token_for(client, "mo")
    resp = client.post("/api/v1/triage/process", json=vignette(), headers=auth(mo))
    assert resp.status_code == 200 and resp.json()["urgency"] == "GREEN"


def test_d3_withdrawal_during_triage_waits_and_run_references_grant(tmp_path, monkeypatch):
    """Service-level race on two connections: while connection A's triage transaction (BEGIN IMMEDIATE)
    is open, a withdrawal on connection B cannot commit ("database is locked" with a short timeout).
    A's run records the grant it relied on; after A commits, B's withdrawal succeeds and further triage
    is denied."""
    db = tmp_path / "race.db"
    anm = Principal(user_id=demo_user_id("anm_demo"), username="anm_demo", role=Role.ANM)
    observed: dict[str, object] = {}

    async def scenario():
        a = await _connect(db)
        b = await _connect(db, timeout=0.1)
        try:
            await run_migrations(a)
            cid = (await consent.create_case(a, anm, consent.CaseCreate(scenario="opd", facility_code="PHC-1"), None))["case_id"]
            await consent.record_decision(a, anm, cid, consent.ConsentDecision(decision="grant", language="en", notice_version=NOTICE_VERSION), None)

            original_require = consent.require

            async def require_then_race(conn, case_id, purpose):
                snap = await original_require(conn, case_id, purpose)
                try:  # we are inside A's write transaction here
                    await consent.withdraw(b, anm, case_id, consent.WithdrawRequest(purpose="triage"), None)
                    observed["withdraw_during"] = "committed"
                except sqlite3.OperationalError as exc:
                    observed["withdraw_during"] = "locked" if "locked" in str(exc) else str(exc)
                return snap

            monkeypatch.setattr(case_triage.consent, "require", require_then_race)
            await case_triage.run_case_triage(a, anm, cid, TriageInput(**vignette()), None)
            monkeypatch.setattr(case_triage.consent, "require", original_require)

            await consent.withdraw(b, anm, cid, consent.WithdrawRequest(purpose="triage"), None)  # now succeeds
            from app.errors import ApiError

            try:
                await case_triage.run_case_triage(a, anm, cid, TriageInput(**vignette()), None)
                observed["after"] = "allowed"
            except ApiError as exc:
                observed["after"] = exc.code
            async with a.execute("SELECT consent_seq FROM triage_runs WHERE case_id = ?", (cid,)) as cur:
                observed["runs"] = [r[0] for r in await cur.fetchall()]
        finally:
            await a.close()
            await b.close()

    asyncio.run(scenario())
    assert observed["withdraw_during"] == "locked"
    assert observed["runs"] == [1]  # the single run references the grant event (seq 1)
    assert observed["after"] == "CONSENT_REQUIRED"
