"""Case access is enforced separately from consent; unauthorized and unknown cases look identical.

These tests use distinct synthetic principals to validate the authorization code. They do not
establish security when real people share one demo account (docs/11: demo-only limitation).
"""

from app.consent_notice import NOTICE_VERSION
import uuid

import pytest

from tests.privacy.helpers import (
    audit_rows,
    auth,
    grant,
    new_case,
    token_for,
    triage,
    withdraw,
)


@pytest.fixture
def owned(client):
    anm = token_for(client, "anm")
    cid = new_case(client, anm)
    grant(client, anm, cid)
    return anm, cid


def _calls(client, token, cid):
    return {
        "grant": lambda: grant(client, token, cid),
        "decline": lambda: client.post(f"/api/v1/cases/{cid}/consent", json={"decision": "decline", "language": "en", "notice_version": "x"}, headers=auth(token)),
        "withdraw": lambda: withdraw(client, token, cid, "triage"),
        "history": lambda: client.get(f"/api/v1/cases/{cid}/consent", headers=auth(token)),
        "read": lambda: client.get(f"/api/v1/cases/{cid}", headers=auth(token)),
        "triage": lambda: triage(client, token, cid),
    }


@pytest.mark.parametrize("op", ["grant", "withdraw", "history", "read", "triage"])
def test_other_account_gets_same_404_as_unknown_case(client, owned, op):
    _, cid = owned
    other = token_for(client, "anm_other")
    before = len(audit_rows(client))
    theirs = _calls(client, other, cid)[op]()
    unknown = _calls(client, other, str(uuid.uuid4()))[op]()
    assert theirs.status_code == unknown.status_code == 404
    strip = lambda r: {k: v for k, v in r.json()["error"].items() if k != "request_id"}  # noqa: E731
    assert strip(theirs) == strip(unknown)
    assert len(audit_rows(client)) == before  # no audit rows for unauthorized or unknown ids


def test_decline_by_other_account_is_404(client, owned):
    _, cid = owned
    other = token_for(client, "anm_other")
    body = {"decision": "decline", "language": "en", "notice_version": NOTICE_VERSION}
    assert client.post(f"/api/v1/cases/{cid}/consent", json=body, headers=auth(other)).status_code == 404


def test_patient_cannot_run_case_triage(client):
    patient = token_for(client, "patient")
    cid = new_case(client, patient)
    grant(client, patient, cid)
    assert triage(client, patient, cid).status_code == 403


def test_medical_officer_can_read_and_triage_but_not_record_consent(client, owned):
    _, cid = owned
    mo = token_for(client, "mo")
    assert client.get(f"/api/v1/cases/{cid}", headers=auth(mo)).status_code == 200
    assert triage(client, mo, cid).status_code == 200
    assert grant(client, mo, cid).status_code == 403


def test_supervisor_can_read_but_not_triage(client, owned):
    _, cid = owned
    sup = token_for(client, "supervisor")
    assert client.get(f"/api/v1/cases/{cid}", headers=auth(sup)).status_code == 200
    assert triage(client, sup, cid).status_code == 403


def test_malformed_case_id_is_rejected_without_echo(client):
    anm = token_for(client, "anm")
    resp = client.get("/api/v1/cases/9876543210-not-a-uuid", headers=auth(anm))
    assert resp.status_code == 400
    assert "9876543210" not in resp.text


def test_unauthenticated_requests_are_401(client, owned):
    _, cid = owned
    assert client.get(f"/api/v1/cases/{cid}").status_code == 401
    assert client.post("/api/v1/cases", json={"scenario": "opd", "facility_code": "PHC-1"}).status_code == 401
