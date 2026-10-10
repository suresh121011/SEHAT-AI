"""Patient-to-ANM handover and the stored body map (docs/11 §3a). Synthetic data only.

Distinct synthetic principals (`anm_other`, `patient_other`) exercise the per-account checks in code; the shared demo
accounts cannot separate real people (docs/11 limitation).
"""

import asyncio
import json
import sqlite3

import pytest

from app.config import get_settings
from app.database import MIGRATIONS, _connect, run_migrations
from tests.privacy.helpers import audit_rows, auth, grant, new_case, token_for, triage, withdraw

API = "/api/v1"


def _token_of(client, tok, cid) -> str:
    return client.get(f"{API}/cases/{cid}", headers=auth(tok)).json()["patient_token"]


def hand_over(client, tok, patient_token):
    return client.post(f"{API}/cases/handover", json={"patient_token": patient_token}, headers=auth(tok))


def put_map(client, tok, cid, regions):
    return client.put(f"{API}/cases/{cid}/body-map", json={"regions": regions}, headers=auth(tok))


@pytest.fixture
def patient_case(client):
    """A case started and consented from a patient account."""
    pat = token_for(client, "patient")
    cid = new_case(client, pat)
    assert grant(client, pat, cid).status_code == 200
    return pat, cid, _token_of(client, pat, cid)


# ── Handover ─────────────────────────────────────────────────────────────


def test_before_handover_an_anm_cannot_reach_a_patient_case(client, patient_case):
    _, cid, _ = patient_case
    anm = token_for(client, "anm")
    assert client.get(f"{API}/cases/{cid}", headers=auth(anm)).status_code == 404
    assert triage(client, anm, cid).status_code == 404
    assert put_map(client, anm, cid, ["chest_left"]).status_code == 404


def test_anm_takes_over_then_completes_the_case_and_patient_still_cannot_triage(client, patient_case):
    pat, cid, code = patient_case
    anm = token_for(client, "anm")
    resp = hand_over(client, anm, code)
    assert resp.status_code == 200, resp.text
    assert resp.json()["case_id"] == cid

    view = client.get(f"{API}/cases/{cid}", headers=auth(anm)).json()
    assert view["is_handler"] is True and view["is_creator"] is False
    assert view["started_by_role"] == "patient" and view["handed_over"] is True
    assert put_map(client, anm, cid, ["abdomen_lower"]).status_code == 200
    assert triage(client, anm, cid).status_code == 200
    # The handover grants nothing to the patient account: triage stays staff-only.
    assert triage(client, pat, cid).status_code == 403


def test_handover_is_audited_with_actor_and_without_the_case_code(client, patient_case):
    _, cid, code = patient_case
    assert hand_over(client, token_for(client, "anm"), code).status_code == 200
    rows = [r for r in audit_rows(client) if r["action"] == "case_handed_over"]
    assert len(rows) == 1
    assert rows[0]["case_id"] == cid and rows[0]["actor_role"] == "anm" and rows[0]["outcome"] == "success"
    assert code not in json.dumps(rows[0])


def test_handover_is_once_per_case_and_idempotent_for_the_same_anm(client, patient_case):
    _, cid, code = patient_case
    anm = token_for(client, "anm")
    assert hand_over(client, anm, code).status_code == 200
    assert hand_over(client, anm, code).status_code == 200  # repeat: no second audit row
    assert len([r for r in audit_rows(client) if r["action"] == "case_handed_over"]) == 1
    other = token_for(client, "anm_other")
    resp = hand_over(client, other, code)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CASE_ALREADY_HANDED_OVER"
    assert client.get(f"{API}/cases/{cid}", headers=auth(other)).status_code == 404


@pytest.mark.parametrize("who", ["patient", "mo", "supervisor"])
def test_only_anm_accounts_can_take_over(client, patient_case, who):
    _, _, code = patient_case
    assert hand_over(client, token_for(client, who), code).status_code == 403


def test_handover_needs_authentication(client, patient_case):
    assert client.post(f"{API}/cases/handover", json={"patient_token": patient_case[2]}).status_code == 401


def test_unknown_staff_created_and_malformed_codes_are_refused(client):
    anm = token_for(client, "anm")
    assert hand_over(client, anm, "PT-000000000000").status_code == 404
    staff_cid = new_case(client, anm)
    grant(client, anm, staff_cid)
    # An ANM-created case is never handed over (to another ANM either): same 404 as an unknown code.
    assert hand_over(client, token_for(client, "anm_other"), _token_of(client, anm, staff_cid)).status_code == 404
    for bad in ("pt-abc", "PT-ZZZZZZZZZZZZ", "' OR 1=1 --", ""):
        assert hand_over(client, anm, bad).status_code in (400, 422)
    assert not [r for r in audit_rows(client) if r["action"] == "case_handed_over"]


@pytest.mark.parametrize("consent_state", ["none", "withdrawn"])
def test_handover_needs_triage_consent_in_effect(client, consent_state):
    pat = token_for(client, "patient")
    cid = new_case(client, pat)
    if consent_state == "withdrawn":
        grant(client, pat, cid)
        withdraw(client, pat, cid, "triage")
    anm = token_for(client, "anm")
    resp = hand_over(client, anm, _token_of(client, pat, cid))
    assert resp.status_code == 403
    assert client.get(f"{API}/cases/{cid}", headers=auth(anm)).status_code == 404  # nothing was claimed
    assert [r for r in audit_rows(client) if r["action"] == "consent_denied" and r["case_id"] == cid]


def test_handler_cannot_record_or_withdraw_the_patients_consent(client, patient_case):
    _, cid, code = patient_case
    anm = token_for(client, "anm")
    hand_over(client, anm, code)
    assert grant(client, anm, cid).status_code == 404
    assert withdraw(client, anm, cid, "triage").status_code == 404


def test_handover_respects_facility_scope(client, monkeypatch, patient_case):
    _, cid, code = patient_case  # created in PHC-KHURDA-01
    monkeypatch.setenv("ACCOUNT_FACILITIES", "anm_demo=PHC-PURI-02;patient_demo=PHC-KHURDA-01")
    get_settings.cache_clear()
    anm = token_for(client, "anm")
    assert hand_over(client, anm, code).status_code == 404
    assert client.get(f"{API}/cases/{cid}", headers=auth(anm)).status_code == 404


# ── Body map ─────────────────────────────────────────────────────────────


def test_body_map_saved_by_patient_is_read_by_the_anm_after_handover(client, patient_case):
    pat, cid, code = patient_case
    assert put_map(client, pat, cid, ["head_front", "chest_left"]).status_code == 200
    assert put_map(client, pat, cid, ["chest_left"]).status_code == 200  # latest wins
    anm = token_for(client, "anm")
    hand_over(client, anm, code)
    got = client.get(f"{API}/cases/{cid}/body-map", headers=auth(anm)).json()
    assert got["regions"] == ["chest_left"] and got["recorded_by_role"] == "patient"
    rows = [r for r in audit_rows(client) if r["action"] == "body_map_recorded"]
    assert len(rows) == 2 and json.loads(rows[-1]["details_json"]) == {"region_count": 1}


def test_body_map_is_not_triage_input(client, patient_case):
    pat, cid, code = patient_case
    put_map(client, pat, cid, ["chest_left", "chest_right"])
    anm = token_for(client, "anm")
    hand_over(client, anm, code)
    resp = triage(client, anm, cid)
    assert resp.status_code == 200
    assert "chest_left" not in json.dumps(resp.json())


def test_another_patient_account_cannot_read_or_write_the_body_map(client, patient_case):
    _, cid, _ = patient_case
    other = token_for(client, "patient_other")
    assert client.get(f"{API}/cases/{cid}/body-map", headers=auth(other)).status_code == 404
    assert put_map(client, other, cid, ["chest_left"]).status_code == 404


@pytest.mark.parametrize("who", ["mo", "supervisor"])
def test_reviewers_read_but_cannot_write_the_body_map(client, patient_case, who):
    _, cid, _ = patient_case
    tok = token_for(client, who)
    assert client.get(f"{API}/cases/{cid}/body-map", headers=auth(tok)).status_code == 200
    assert put_map(client, tok, cid, ["chest_left"]).status_code == 403


def test_body_map_needs_consent_but_earlier_selection_stays_readable(client, patient_case):
    pat, cid, _ = patient_case
    put_map(client, pat, cid, ["back_lower"])
    withdraw(client, pat, cid, "triage")
    assert put_map(client, pat, cid, ["neck_front"]).status_code == 403
    assert client.get(f"{API}/cases/{cid}/body-map", headers=auth(pat)).json()["regions"] == ["back_lower"]


@pytest.mark.parametrize("regions", [["liver"], ["chest_left", "chest_left"], ["<script>"], "chest_left"])
def test_body_map_rejects_unknown_duplicate_and_malformed_regions(client, patient_case, regions):
    pat, cid, _ = patient_case
    assert client.put(f"{API}/cases/{cid}/body-map", json={"regions": regions}, headers=auth(pat)).status_code in (400, 422)


def test_body_map_needs_authentication(client, patient_case):
    _, cid, _ = patient_case
    assert client.get(f"{API}/cases/{cid}/body-map").status_code == 401
    assert client.put(f"{API}/cases/{cid}/body-map", json={"regions": []}).status_code == 401


# ── Migration ────────────────────────────────────────────────────────────


def test_migration_backfills_creator_role_from_the_case_created_audit_row(tmp_path):
    db = tmp_path / "v11.db"

    async def migrate(steps):
        conn = await _connect(db)
        try:
            return await run_migrations(conn, steps)
        finally:
            await conn.close()

    asyncio.run(migrate(MIGRATIONS[:11]))
    with sqlite3.connect(db) as c:
        for cid, role in (("c-pat", "patient"), ("c-anm", "anm"), ("c-none", None)):
            c.execute("INSERT INTO cases (case_id, patient_token, facility_code, scenario, status, created_by) VALUES (?, ?, 'PHC-KHURDA-01', 'opd', 'open', 'u')",
                      (cid, f"PT-{cid}"))
            if role:
                c.execute("INSERT INTO audit_log (event_id, timestamp, actor_id, actor_role, action, case_id, outcome, details_json, previous_hash, current_hash) "
                          "VALUES (?, 't', 'u', ?, 'case_created', ?, 'success', '{}', 'p', ?)", (f"e-{cid}", role, cid, f"h-{cid}"))
    assert asyncio.run(migrate(MIGRATIONS)) == len(MIGRATIONS)
    with sqlite3.connect(db) as c:
        got = dict(c.execute("SELECT case_id, created_by_role FROM cases").fetchall())
    assert got == {"c-pat": "patient", "c-anm": "anm", "c-none": None}
