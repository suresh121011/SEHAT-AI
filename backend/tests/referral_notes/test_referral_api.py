"""Referral closure tracking API (docs/21, architecture §16, docs/06 §2.5): create a referral from a signed-off triage
run, record its status forward to closure, flag it overdue after 48 hours without an update, print its letter.

What must hold (docs/implementation/h0-1-wip-routers.md lists what the old router lacked): only a medical officer
refers; case access and facility scope as every case route; triage consent in effect, a denial audited; closed lists
only; append-only status events; one audit event per action with codes only; supervisors see metadata, never the
clinical letter. Synthetic data only."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import guard
from app.config import get_settings
from app.referral_notes import service
from tests.privacy.helpers import audit_rows, auth, token_for, triage, withdraw
from tests.review.test_facility import A, B, MAPPING, iso, remap, seed  # noqa: F401  (fixture)
from tests.review.test_review_api import RED, YELLOW, anm, make_case, mo, override, sup  # noqa: F401  (fixtures)

API = "/api/v1"


def sign_off(client, mo, cid, run_id):
    r = client.patch(f"{API}/triage/{cid}/sign-off", json={"triage_run_id": run_id, "confirm": True}, headers=auth(mo))
    assert r.status_code == 200, r.text


def body(run_id: str, **overrides) -> dict:
    data = {"triage_run_id": run_id, "to_facility": "DH-BBSR-01", "reason_code": "emergency_care", "transport_mode": "108_ambulance",
            "escort_required": True, "travel_minutes": 45, "insurance_scheme": "pmjay", "confirm": True}
    data.update(overrides)
    return data


def refer(client, token, cid, run_id, **overrides):
    return client.post(f"{API}/cases/{cid}/referrals", json=body(run_id, **overrides), headers=auth(token))


def referred(client, anm, mo, inputs=RED) -> tuple[str, str, dict]:
    cid, run_id = make_case(client, anm, inputs)
    sign_off(client, mo, cid, run_id)
    r = refer(client, mo, cid, run_id)
    assert r.status_code == 201, r.text
    return cid, run_id, r.json()


def move(client, token, cid, rid, status, expected, detail=None):
    return client.post(f"{API}/cases/{cid}/referrals/{rid}/status",
                       json={"status": status, "expected_status": expected, "detail_code": detail, "confirm": True}, headers=auth(token))


def rows(sql: str, *args) -> list:
    with sqlite3.connect(get_settings().database_path) as c:
        return c.execute(sql, args).fetchall()


# ── Creating a referral ──────────────────────────────────────────────────────────────────────────────────────────
def test_medical_officer_refers_a_signed_off_case(client, anm, mo):
    cid, run_id, ref = referred(client, anm, mo)
    assert ref["status"] == "referred" and ref["urgency"] == "RED" and ref["triage_run_id"] == run_id
    assert ref["from_facility"]["code"] == "PHC-KHURDA-01" and ref["to_facility"]["name"] == "District Hospital, Bhubaneswar"
    assert ref["reason"] == {"code": "emergency_care", "label": "Emergency care at a higher facility"}
    assert ref["transport"] == {"mode": "108_ambulance", "label": "108 ambulance", "escort_required": True, "travel_minutes": 45}
    assert ref["insurance"]["scheme"] == "pmjay" and ref["overdue"] is False and ref["overdue_threshold_hours"] == 48
    assert ref["allowed_next"] == ["in_transit", "reached", "not_reached"]
    assert [h["status"] for h in ref["status_history"]] == ["referred"]
    view = client.get(f"{API}/cases/{cid}/referrals", headers=auth(mo)).json()
    assert [r["referral_id"] for r in view["referrals"]] == [ref["referral_id"]]
    assert view["can_refer"] is False and view["refer_blocked"] == "referral_open"
    assert "patient_name" not in str(view) and ref["patient_token"]


def test_referral_needs_the_latest_run_signed_off(client, anm, mo):
    cid, run_id = make_case(client, anm, RED)
    r = refer(client, mo, cid, run_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SIGN_OFF_REQUIRED"
    assert client.get(f"{API}/cases/{cid}/referrals", headers=auth(mo)).json()["refer_blocked"] == "sign_off_required"
    sign_off(client, mo, cid, run_id)
    newer = triage(client, anm, cid, vitals={"resp_rate": 31})
    assert newer.status_code == 200
    r = refer(client, mo, cid, run_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN"
    assert rows("SELECT COUNT(*) FROM referral_packets")[0][0] == 0


@pytest.mark.parametrize("who", ["anm", "supervisor", "patient"])
def test_only_a_medical_officer_can_refer(client, anm, mo, who):
    cid, run_id = make_case(client, anm, RED)
    sign_off(client, mo, cid, run_id)
    r = refer(client, token_for(client, who), cid, run_id)
    assert r.status_code == 403


def test_referral_needs_triage_consent_and_the_refusal_is_audited(client, anm, mo):
    cid, run_id = make_case(client, anm, RED)
    sign_off(client, mo, cid, run_id)
    assert withdraw(client, anm, cid, "triage").status_code == 200
    r = refer(client, mo, cid, run_id)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert any(a["action"] == "consent_denied" and a["case_id"] == cid for a in audit_rows(client))
    assert rows("SELECT COUNT(*) FROM referral_packets")[0][0] == 0


def test_one_open_referral_per_case(client, anm, mo):
    cid, run_id, _ = referred(client, anm, mo)
    r = refer(client, mo, cid, run_id, to_facility="CHC-JATNI-01")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFERRAL_OPEN"


def test_destination_is_a_listed_facility_other_than_the_origin(client, anm, mo):
    cid, run_id = make_case(client, anm, RED)
    sign_off(client, mo, cid, run_id)
    assert refer(client, mo, cid, run_id, to_facility="PHC-KHURDA-01").status_code == 400  # not a destination
    assert refer(client, mo, cid, run_id, to_facility="XX-NOWHERE-9").status_code == 400
    assert refer(client, mo, cid, run_id, reason_code="see a cardiologist").status_code == 400  # closed lists only
    assert refer(client, mo, cid, run_id, travel_minutes=0).status_code == 400
    assert refer(client, mo, cid, run_id, confirm=False).status_code == 400


# ── Status to closure ────────────────────────────────────────────────────────────────────────────────────────────
def test_status_moves_forward_to_closure_and_rows_are_append_only(client, anm, mo):
    cid, _, ref = referred(client, anm, mo)
    rid = ref["referral_id"]
    assert move(client, anm, cid, rid, "seen", "referred").json()["error"]["code"] == "INVALID_TRANSITION"
    assert move(client, anm, cid, rid, "reached", "in_transit").json()["error"]["code"] == "STALE_REFERRAL_STATUS"
    for status, expected in (("in_transit", "referred"), ("reached", "in_transit"), ("seen", "reached")):
        r = move(client, anm if status != "seen" else mo, cid, rid, status, expected)
        assert r.status_code == 200, r.text
    r = move(client, mo, cid, rid, "outcome_recorded", "seen", "admitted")
    assert r.status_code == 200 and r.json()["status"] == "outcome_recorded" and r.json()["allowed_next"] == []
    history = r.json()["status_history"]
    assert [h["status"] for h in history] == ["referred", "in_transit", "reached", "seen", "outcome_recorded"]
    assert history[-1]["detail_code"] == "admitted" and history[-1]["detail_label"] == "Admitted"
    assert move(client, mo, cid, rid, "seen", "outcome_recorded").json()["error"]["code"] == "INVALID_TRANSITION"
    with sqlite3.connect(get_settings().database_path) as c:
        for sql in ("UPDATE referral_events SET status = 'referred'", "DELETE FROM referral_events",
                    "UPDATE referral_packets SET to_facility = 'X'", "DELETE FROM referral_packets"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                c.execute(sql)
    # a closed referral lets the medical officer refer the case again
    assert client.get(f"{API}/cases/{cid}/referrals", headers=auth(mo)).json()["can_refer"] is True


def test_closure_codes_are_required_and_from_closed_lists(client, anm, mo):
    cid, _, ref = referred(client, anm, mo)
    rid = ref["referral_id"]
    assert move(client, anm, cid, rid, "not_reached", "referred").status_code == 400  # a barrier code is required
    assert move(client, anm, cid, rid, "not_reached", "referred", "the road was closed").status_code == 400
    assert move(client, anm, cid, rid, "in_transit", "referred", "admitted").status_code == 400  # no code on this step
    r = move(client, anm, cid, rid, "not_reached", "referred", "transport")
    assert r.status_code == 200 and r.json()["status"] == "not_reached" and r.json()["status_history"][-1]["detail_label"] == "No transport"


def test_the_creating_anm_and_a_medical_officer_update_status_nobody_else(client, anm, mo, sup):
    cid, _, ref = referred(client, anm, mo)
    rid = ref["referral_id"]
    assert move(client, token_for(client, "anm_other"), cid, rid, "in_transit", "referred").status_code == 404
    assert move(client, sup, cid, rid, "in_transit", "referred").status_code == 403
    assert move(client, token_for(client, "patient"), cid, rid, "in_transit", "referred").status_code == 403
    assert move(client, anm, cid, rid, "in_transit", "referred").status_code == 200


def test_unknown_or_foreign_referral_ids_are_not_found(client, anm, mo):
    cid, _, ref = referred(client, anm, mo)
    other_cid, other_run = make_case(client, anm, RED)
    assert move(client, mo, other_cid, ref["referral_id"], "in_transit", "referred").status_code == 404
    missing = "00000000-0000-4000-8000-000000000000"
    assert move(client, mo, cid, missing, "in_transit", "referred").status_code == 404
    assert client.get(f"{API}/cases/{cid}/referrals/{missing}/letter", headers=auth(mo)).status_code == 404


def test_every_referral_action_is_audited_with_codes_only(client, anm, mo):
    cid, _, ref = referred(client, anm, mo)
    move(client, anm, cid, ref["referral_id"], "in_transit", "referred")
    events = [a for a in audit_rows(client) if a["action"].startswith("referral_")]
    assert [e["action"] for e in events] == ["referral_created", "referral_status_recorded"]
    created = json.loads(events[0]["details_json"])
    assert created == {"referral_id": ref["referral_id"], "triage_run_id": ref["triage_run_id"], "urgency": "RED", "to_facility": "DH-BBSR-01"}
    assert json.loads(events[1]["details_json"]) == {"referral_id": ref["referral_id"], "from_status": "referred", "to_status": "in_transit", "has_detail": False}
    assert all(e["actor_role"] in ("medical_officer", "anm") for e in events)


def test_supervisor_audit_view_carries_no_referral_codes(client, anm, mo, sup):
    """The audit log is supervisor-readable (GET /audit/{case_id}), so its referral details hold ids, urgency and the
    destination only; reason, transport, insurance and outcome codes stay in the referral tables."""
    cid, _, ref = referred(client, anm, mo)
    for status, expected, detail in (("reached", "referred", None), ("seen", "reached", None), ("outcome_recorded", "seen", "admitted")):
        assert move(client, mo, cid, ref["referral_id"], status, expected, detail).status_code == 200
    r = client.get(f"{API}/audit/{cid}", headers=auth(sup))
    assert r.status_code == 200 and "referral_created" in r.text
    for code in ("emergency_care", "pmjay", "108_ambulance", "admitted", "escort"):
        assert code not in r.text, code


def test_packet_urgency_is_raised_by_an_override_and_never_lowered(client, anm, mo):
    cid, run_id = make_case(client, anm, YELLOW)
    assert override(client, mo, cid, run_id, "RED", code="clinical_reassessment").status_code == 200
    sign_off(client, mo, cid, run_id)
    raised = refer(client, mo, cid, run_id).json()
    assert raised["urgency"] == "RED"
    letter = client.get(f"{API}/cases/{cid}/referrals/{raised['referral_id']}/letter", headers=auth(mo)).json()
    assert letter["urgency"]["source"] == "reviewer_raise" and letter["urgency"]["rules_urgency"] == "YELLOW"
    cid2, run2 = make_case(client, anm, RED)
    assert override(client, mo, cid2, run2, "YELLOW", code="other", text="Reassessed at bedside, stable").status_code == 200
    sign_off(client, mo, cid2, run2)
    kept = refer(client, mo, cid2, run2).json()
    assert kept["urgency"] == "RED"  # a lower reviewer value never lowers the referral
    letter2 = client.get(f"{API}/cases/{cid2}/referrals/{kept['referral_id']}/letter", headers=auth(mo)).json()
    assert letter2["urgency"]["source"] == "rules_engine" and letter2["urgency"]["reviewer_recorded"] == "YELLOW"


# ── Overdue and tracking lists ───────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def later(monkeypatch):
    base = datetime.now(timezone.utc)
    state = {"offset": timedelta(0)}
    monkeypatch.setattr(service, "_now", lambda: base + state["offset"])

    def advance(hours: float) -> None:
        state["offset"] += timedelta(hours=hours)
    return advance


def test_overdue_after_48_hours_without_an_update_until_the_patient_arrives(client, anm, mo, later):
    cid, _, ref = referred(client, anm, mo)
    rid = ref["referral_id"]
    later(47.5)
    assert client.get(f"{API}/referral/overdue", headers=auth(mo)).json()["items"] == []
    later(1)
    overdue = client.get(f"{API}/referral/overdue", headers=auth(mo)).json()
    assert [i["referral_id"] for i in overdue["items"]] == [rid] and overdue["items"][0]["hours_since_update"] == 48
    assert move(client, anm, cid, rid, "in_transit", "referred").status_code == 200  # an update restarts the clock
    assert client.get(f"{API}/referral/overdue", headers=auth(mo)).json()["items"] == []
    later(49)
    assert len(client.get(f"{API}/referral/overdue", headers=auth(mo)).json()["items"]) == 1
    assert move(client, anm, cid, rid, "reached", "in_transit").status_code == 200
    later(200)
    active = client.get(f"{API}/referral/active", headers=auth(mo)).json()
    assert [(i["status"], i["overdue"]) for i in active["items"]] == [("reached", False)]
    assert active["counts"] == {"referred": 0, "in_transit": 0, "reached": 1, "seen": 0, "overdue": 0}


def test_tracking_lists_leave_out_closed_referrals_and_are_for_reviewers(client, anm, mo, sup):
    cid, _, ref = referred(client, anm, mo)
    assert len(client.get(f"{API}/referral/active", headers=auth(sup)).json()["items"]) == 1
    move(client, anm, cid, ref["referral_id"], "not_reached", "referred", "cost")
    assert client.get(f"{API}/referral/active", headers=auth(mo)).json()["items"] == []
    assert client.get(f"{API}/referral/active", headers=auth(anm)).status_code == 403
    assert client.get(f"{API}/referral/active", headers=auth(token_for(client, "patient"))).status_code == 403


def test_supervisor_sees_tracking_metadata_but_no_clinical_content(client, anm, mo, sup):
    cid, _, ref = referred(client, anm, mo)
    item = client.get(f"{API}/referral/active", headers=auth(sup)).json()["items"][0]
    assert item["referral_id"] == ref["referral_id"] and item["urgency"] == "RED" and item["status"] == "referred"
    view = client.get(f"{API}/cases/{cid}/referrals", headers=auth(sup)).json()
    meta = view["referrals"][0]
    assert view["clinical_content_available"] is False and "options" not in view
    assert "reason" not in meta and "transport" not in meta and "insurance" not in meta
    assert client.get(f"{API}/cases/{cid}/referrals/{ref['referral_id']}/letter", headers=auth(sup)).status_code == 403
    assert client.get(f"{API}/cases/{cid}/referrals/{ref['referral_id']}/fhir", headers=auth(sup)).status_code == 403
    assert "resp_rate" not in str(view) and "emergency_care" not in str(view)


# ── The letter ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_letter_is_built_from_the_signed_off_run_and_never_changes_afterwards(client, anm, mo):
    cid, run_id, ref = referred(client, anm, mo)
    url = f"{API}/cases/{cid}/referrals/{ref['referral_id']}/letter"
    letter = client.get(url, headers=auth(mo)).json()
    assert letter["triage"]["run_id"] == run_id and letter["urgency"]["level"] == "RED"
    assert [f["rule_id"] for f in letter["flags"]] == ["ATP_RED_RR", "NEWS2_LOW_MEDIUM"]
    assert letter["flags"][0]["source"].startswith("Singh SK") and letter["flags"][0]["evidence"][0]["value"] == 30
    vitals = {v["field"]: v["value"] for v in letter["vitals"]}
    assert vitals["resp_rate"] == 30 and vitals["blood_pressure"] == "124/80" and vitals["spo2"] == 98
    assert letter["sign_off"]["reviewer_role"] == "medical_officer" and letter["sign_off"]["signed_off_at"]
    assert "mo_demo" not in str(letter)  # role and time, never an account id (as everywhere in the review views)
    assert letter["to_facility"]["code"] == "DH-BBSR-01" and letter["patient_token"] == ref["patient_token"]
    assert triage(client, anm, cid, vitals={"resp_rate": 12}).status_code == 200  # a later re-triage
    again = client.get(url, headers=auth(mo)).json()
    assert again["flags"] == letter["flags"] and again["vitals"] == letter["vitals"]
    assert client.get(url, headers=auth(anm)).status_code == 200  # the creating ANM can print it too


def test_letter_wording_passes_the_non_diagnostic_guard(client, anm, mo):
    cid, _, ref = referred(client, anm, mo)
    letter = client.get(f"{API}/cases/{cid}/referrals/{ref['referral_id']}/letter", headers=auth(mo)).json()
    # Every string the letter assembles is checked; the fixed disclaimer is not, because the guard rightly flags any
    # sentence with the word "diagnosis", including the one that says this is not one.
    texts = [letter["title"], letter["reason"]["label"], letter["transport"]["label"], letter["insurance"]["label"],
             letter["scenario"]["label"], letter["urgency"]["meaning"], letter["sign_off"]["statement"]]
    texts += [v["label"] for v in letter["vitals"]] + [h["label"] for h in letter["status_history"]]
    texts += [f["reason"] for f in letter["flags"]]
    for text in texts:
        assert guard.check(text) is None, text
    assert "not a diagnosis" in letter["disclaimer"] and "research prototype" in letter["disclaimer"]


def test_tracking_lists_follow_the_facility_scope(iso, monkeypatch):
    anm, mo, sup = token_for(iso, "anm"), token_for(iso, "mo"), token_for(iso, "supervisor")
    cid_a, run_a = seed(iso, anm, A, RED)
    sign_off(iso, mo, cid_a, run_a)
    assert refer(iso, mo, cid_a, run_a).status_code == 201
    remap(monkeypatch, f"anm_demo={A},{B};mo_demo={B};supervisor_demo=*;patient_demo={A}")
    cid_b, run_b = seed(iso, anm, B, RED)
    sign_off(iso, mo, cid_b, run_b)
    assert refer(iso, mo, cid_b, run_b).status_code == 201
    assert [i["case_id"] for i in iso.get(f"{API}/referral/active", headers=auth(mo)).json()["items"]] == [cid_b]
    remap(monkeypatch, MAPPING)
    assert [i["case_id"] for i in iso.get(f"{API}/referral/active", headers=auth(mo)).json()["items"]] == [cid_a]
    assert {i["case_id"] for i in iso.get(f"{API}/referral/active", headers=auth(sup)).json()["items"]} == {cid_a, cid_b}


def test_yellow_case_letter_shows_the_missing_information(client, anm, mo):
    cid, _, ref = referred(client, anm, mo, YELLOW)
    letter = client.get(f"{API}/cases/{cid}/referrals/{ref['referral_id']}/letter", headers=auth(mo)).json()
    assert letter["urgency"]["level"] == "YELLOW" and "vitals.spo2" in letter["missing_fields"]
    assert all(v["field"] != "spo2" for v in letter["vitals"])  # a missing value is never shown as normal
