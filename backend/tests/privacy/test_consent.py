"""Consent recording, state rules, purpose-specific withdrawal (docs/11 §K)."""

import sqlite3

import pytest

from app.config import get_settings
from app.consent_notice import NOTICE_VERSION
from tests.privacy.helpers import (
    audit_rows,
    auth,
    decline,
    grant,
    new_case,
    token_for,
    triage,
    withdraw,
)


@pytest.fixture
def anm(client):
    return token_for(client, "anm")


def _events(case_id):
    with sqlite3.connect(get_settings().database_path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM consent_events WHERE case_id=? ORDER BY seq", (case_id,))]


@pytest.mark.parametrize("language,status", [("en", "project_draft"), ("hi", "draft_unreviewed_translation"), ("or", "draft_unreviewed_translation")])
def test_notice_per_language_has_version_and_review_status(client, anm, language, status):
    body = client.get(f"/api/v1/consent/notice?language={language}", headers=auth(anm)).json()
    assert body["version"] == NOTICE_VERSION
    assert body["review_status"] == status
    assert set(body["purposes"]) == {"triage", "ai_assist", "voice_cloud"}


def test_unsupported_notice_language_rejected(client, anm):
    assert client.get("/api/v1/consent/notice?language=ta", headers=auth(anm)).status_code == 400


def test_initial_state_is_not_provided(client, anm):
    cid = new_case(client, anm)
    assert client.get(f"/api/v1/cases/{cid}", headers=auth(anm)).json()["consent"] == {"triage": "not_provided", "ai_assist": "not_provided", "voice_cloud": "not_provided"}


def test_anm_grant_is_recorded_as_staff_attestation_with_all_bound_fields(client, anm):
    cid = new_case(client, anm)
    resp = grant(client, anm, cid, language="or")
    assert resp.json()["consent"] == {"triage": "granted", "ai_assist": "declined", "voice_cloud": "declined"}
    ev = _events(cid)
    assert [(e["purpose"], e["action"]) for e in ev] == [("triage", "granted"), ("ai_assist", "declined"), ("voice_cloud", "declined")]
    e = ev[0]
    assert (e["method"], e["actor_role"], e["language"], e["notice_version"], e["notice_review_status"]) == (
        "staff_attested_verbal", "anm", "or", NOTICE_VERSION, "draft_unreviewed_translation"
    )
    assert e["created_at"] and e["actor_id"]


def test_patient_grant_uses_patient_button_method(client):
    patient = token_for(client, "patient")
    cid = new_case(client, patient)
    grant(client, patient, cid, ai=True)
    assert {e["method"] for e in _events(cid)} == {"patient_button"}


def test_client_cannot_supply_method_or_actor(client, anm):
    cid = new_case(client, anm)
    for extra in ({"method": "patient_button"}, {"actor_id": "someone"}, {"actor_role": "medical_officer"}):
        body = {"decision": "grant", "language": "en", "notice_version": NOTICE_VERSION, **extra}
        assert client.post(f"/api/v1/cases/{cid}/consent", json=body, headers=auth(anm)).status_code == 400
    assert _events(cid) == []


def test_stale_notice_version_is_409_and_nothing_recorded(client, anm):
    cid = new_case(client, anm)
    resp = grant(client, anm, cid, version="1999-01-01.0")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NOTICE_VERSION_STALE"
    assert _events(cid) == []


def test_decline_blocks_triage(client, anm):
    cid = new_case(client, anm)
    decline(client, anm, cid)
    resp = triage(client, anm, cid)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_decline_then_grant_is_allowed(client, anm):
    cid = new_case(client, anm)
    decline(client, anm, cid)
    assert grant(client, anm, cid).json()["consent"]["triage"] == "granted"
    assert triage(client, anm, cid).status_code == 200


def test_withdraw_ai_assist_keeps_triage(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid, ai=True)
    resp = withdraw(client, anm, cid, "ai_assist")
    assert resp.json()["consent"] == {"triage": "granted", "ai_assist": "withdrawn", "voice_cloud": "declined"}
    assert triage(client, anm, cid).status_code == 200


def test_withdraw_triage_cascades_to_ai_assist_with_explicit_event(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid, ai=True)
    resp = withdraw(client, anm, cid, "triage")
    assert resp.json()["consent"] == {"triage": "withdrawn", "ai_assist": "withdrawn", "voice_cloud": "declined"}
    ev = _events(cid)
    assert [(e["purpose"], e["action"], e["method"]) for e in ev[-2:]] == [
        ("triage", "withdrawn", "staff_attested_verbal"),
        ("ai_assist", "withdrawn", "cascade_from_triage"),
    ]
    assert len(ev) == 5  # history kept, nothing overwritten (3 decision events + 2 withdrawals)
    assert triage(client, anm, cid).status_code == 403


def test_regrant_after_withdrawal(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid)
    withdraw(client, anm, cid, "triage")
    assert grant(client, anm, cid).json()["consent"]["triage"] == "granted"
    assert triage(client, anm, cid).status_code == 200


def test_withdrawing_nothing_is_409(client, anm):
    cid = new_case(client, anm)
    assert withdraw(client, anm, cid, "triage").json()["error"]["code"] == "NOTHING_TO_WITHDRAW"
    grant(client, anm, cid)  # ai_assist declined
    assert withdraw(client, anm, cid, "ai_assist").status_code == 409


def test_decline_with_ai_assist_is_rejected(client, anm):
    cid = new_case(client, anm)
    body = {"decision": "decline", "include_ai_assist": True, "language": "en", "notice_version": NOTICE_VERSION}
    assert client.post(f"/api/v1/cases/{cid}/consent", json=body, headers=auth(anm)).status_code == 400


def test_consent_changes_are_audited_atomically(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid, ai=True)
    withdraw(client, anm, cid, "triage")
    actions = [r["action"] for r in audit_rows(client) if r["case_id"] == cid]
    assert actions == ["case_created", "consent_granted", "consent_declined", "consent_withdrawn"]  # voice_cloud declined


def test_audit_failure_rolls_back_the_consent_change(client, anm, monkeypatch):
    from app import audit

    cid = new_case(client, anm)

    async def broken(*a, **k):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(audit, "record", broken)
    resp = grant(client, anm, cid)
    assert resp.status_code == 500
    assert _events(cid) == []  # consent change rolled back with the failed audit write


def test_history_shows_roles_not_actor_ids(client, anm):
    cid = new_case(client, anm)
    grant(client, anm, cid)
    body = client.get(f"/api/v1/cases/{cid}/consent", headers=auth(anm)).json()
    assert body["history"] and all("actor_id" not in e for e in body["history"])
    assert body["history"][0]["actor_role"] == "anm"
