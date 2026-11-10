"""Patient identity and patient-level authorization (docs/security/post-remediation-hardening.md A3, M-C). Every login
here is a real OIDC session from a local test issuer (no shared demo account). Synthetic data only.

A patient login reaches only visits linked to the patient it was explicitly bound to by an ANM-issued, single-use
code, and only while each visit's `link_visits` consent is in effect. Nothing is matched or merged automatically.
"""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.privacy.helpers import auth, withdraw
from tests.privacy.test_oidc import JWK, NONCE, _oidc_env, _token
from tests.review.test_patient_registry import _case, _new

API = "/api/v1"
A, B = "PHC-KHURDA-01", "PHC-PURI-02"


@pytest.fixture
def oc(tmp_path, monkeypatch):
    _oidc_env(monkeypatch, tmp_path)
    from app.main import create_app

    app = create_app()
    app.state.oidc_jwks = {"keys": [JWK]}
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def login(c, sub, role, facilities=(A,)):
    claims = {"sehat_role": role, "sehat_facilities": list(facilities)} if role != "patient" else {"sehat_role": "patient"}
    r = c.post(f"{API}/auth/oidc/session", json={"id_token": _token(sub=sub, **claims), "nonce": NONCE})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _rows(sql, *a):
    with sqlite3.connect(get_settings().database_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, a)]


def registered_visit(c, anm, facility=A):
    cid = _case(c, anm, facility)
    assert _new(c, anm, cid).status_code == 201
    return cid


def issue(c, anm, cid):
    return c.post(f"{API}/cases/{cid}/patient/binding-code", headers=auth(anm))


def bind(c, tok, code):
    return c.post(f"{API}/me/patient-record/bind", json={"binding_code": code}, headers=auth(tok))


def read(c, tok, cid):
    return c.get(f"{API}/cases/{cid}", headers=auth(tok))


@pytest.fixture
def world(oc):
    anm = login(oc, "staff-anm-1", "anm")
    cid = registered_visit(oc, anm)
    p1, p2 = login(oc, "patient-1", "patient"), login(oc, "patient-2", "patient")
    return oc, anm, cid, p1, p2


# ── binding ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_bound_patient_reads_their_own_visit_and_another_patient_cannot(world):
    c, anm, cid, p1, p2 = world
    assert read(c, p1, cid).status_code == 404  # before binding: nothing
    code = issue(c, anm, cid).json()["binding_code"]
    assert bind(c, p1, code).json() == {"bound": True, "unchanged": False}
    assert read(c, p1, cid).status_code == 200
    rec = c.get(f"{API}/me/patient-record", headers=auth(p1)).json()
    assert rec["bound"] is True and [v["case_id"] for v in rec["visits"]] == [cid] and rec["visits"][0]["is_past_visit"] is True
    assert read(c, p2, cid).status_code == 404
    assert c.get(f"{API}/me/patient-record", headers=auth(p2)).json() == {"bound": False, "visits": [], "note": rec["note"]}


def test_a_code_works_once_and_a_second_patient_cannot_reuse_it(world):
    c, anm, cid, p1, p2 = world
    code = issue(c, anm, cid).json()["binding_code"]
    assert bind(c, p1, code).status_code == 200
    r = bind(c, p2, code)
    assert r.status_code == 404 and r.json()["error"]["code"] == "BINDING_CODE_INVALID"
    assert read(c, p2, cid).status_code == 404


def test_unknown_mistyped_and_expired_codes_look_the_same(world):
    c, anm, cid, p1, _ = world
    code = issue(c, anm, cid).json()["binding_code"]
    typo = code[:-1] + ("0" if code[-1] != "0" else "1")
    with sqlite3.connect(get_settings().database_path) as db:
        db.execute("DROP TRIGGER patient_binding_codes_use_once")  # test-only: move the clock
        past = lambda d: (datetime.now(timezone.utc) - d).isoformat(timespec="microseconds")  # noqa: E731
        db.execute("UPDATE patient_binding_codes SET issued_at = ?, expires_at = ?", (past(timedelta(minutes=16)), past(timedelta(seconds=1))))
    answers = {(r.status_code, r.json()["error"]["code"]) for r in (bind(c, p1, typo), bind(c, p1, code), bind(c, p1, "SB-ZZZZ-ZZZZ"))}
    assert answers == {(404, "BINDING_CODE_INVALID")}
    assert read(c, p1, cid).status_code == 404


def test_a_login_bound_to_one_patient_cannot_silently_switch_to_another(world):
    c, anm, cid, p1, _ = world
    other = registered_visit(c, anm)
    assert bind(c, p1, issue(c, anm, cid).json()["binding_code"]).status_code == 200
    r = bind(c, p1, issue(c, anm, other).json()["binding_code"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_BOUND"
    assert read(c, p1, other).status_code == 404 and read(c, p1, cid).status_code == 200


def test_concurrent_use_of_one_code_binds_exactly_one_login(world):
    c, anm, cid, p1, p2 = world
    code = issue(c, anm, cid).json()["binding_code"]
    results = []
    threads = [threading.Thread(target=lambda t=t: results.append(bind(c, t, code).status_code)) for t in (p1, p2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 404]
    assert len(_rows("SELECT * FROM patient_identity_events WHERE action = 'bind'")) == 1


# ── who may issue and who may bind ────────────────────────────────────────────────────────────────────────────

def test_only_the_visits_anm_issues_codes_and_only_for_a_linked_visit(oc):
    anm, other_anm = login(oc, "staff-anm-1", "anm"), login(oc, "staff-anm-2", "anm")
    mo, far_anm = login(oc, "staff-mo-1", "medical_officer"), login(oc, "staff-anm-3", "anm", facilities=(B,))
    cid = registered_visit(oc, anm)
    assert issue(oc, mo, cid).status_code == 403
    assert issue(oc, other_anm, cid).status_code == 404  # not this visit's ANM: looks missing
    assert issue(oc, far_anm, cid).status_code == 404  # another facility: looks missing
    unlinked = _case(oc, anm)
    assert issue(oc, anm, unlinked).status_code == 409


def test_staff_can_never_bind(oc):
    anm = login(oc, "staff-anm-1", "anm")
    code = issue(oc, anm, registered_visit(oc, anm)).json()["binding_code"]
    r = bind(oc, anm, code)
    assert r.status_code == 403 and r.json()["error"]["code"] == "IDENTITY_REQUIRED"


def test_a_shared_demo_patient_account_cannot_bind(client):
    from tests.privacy.helpers import token_for

    r = client.post(f"{API}/me/patient-record/bind", json={"binding_code": "SB-0000-0000"}, headers=auth(token_for(client, "patient")))
    assert r.status_code == 403 and r.json()["error"]["code"] == "IDENTITY_REQUIRED"


def test_a_bound_patient_cannot_write_triage_or_review(world):
    c, anm, cid, p1, _ = world
    bind(c, p1, issue(c, anm, cid).json()["binding_code"])
    assert c.post(f"{API}/cases/{cid}/consent/withdraw", json={"purpose": "triage"}, headers=auth(p1)).status_code in (403, 404)
    assert c.put(f"{API}/cases/{cid}/body-map", json={"regions": []}, headers=auth(p1)).status_code in (403, 404)
    assert c.get(f"{API}/triage/{cid}", headers=auth(p1)).status_code in (403, 404)


# ── access ends when the link, the consent or the binding ends ────────────────────────────────────────────────

def test_withdrawing_link_visits_consent_ends_the_patients_access(world):
    c, anm, cid, p1, _ = world
    bind(c, p1, issue(c, anm, cid).json()["binding_code"])
    assert withdraw(c, anm, cid, "link_visits").status_code == 200
    assert read(c, p1, cid).status_code == 404
    assert c.get(f"{API}/me/patient-record", headers=auth(p1)).json()["visits"] == []


def test_unlinking_the_visit_ends_the_patients_access(world):
    c, anm, cid, p1, _ = world
    bind(c, p1, issue(c, anm, cid).json()["binding_code"])
    r = c.post(f"{API}/cases/{cid}/patient/unlink", json={"reason_code": "linked_in_error", "confirm": True}, headers=auth(anm))
    assert r.status_code == 200
    assert read(c, p1, cid).status_code == 404


def test_unbinding_by_the_patient_or_an_admin_ends_access(world):
    c, anm, cid, p1, p2 = world
    bind(c, p1, issue(c, anm, cid).json()["binding_code"])
    assert c.post(f"{API}/me/patient-record/unbind", json={"confirm": True}, headers=auth(p1)).json() == {"unbound": True}
    assert read(c, p1, cid).status_code == 404
    bind(c, p2, issue(c, anm, cid).json()["binding_code"])
    admin = login(c, "staff-admin-1", "admin", facilities=("*",))
    p2_user = c.get(f"{API}/auth/me", headers=auth(p2)).json()["user_id"]
    assert c.post(f"{API}/admin/users/{p2_user}/patient-binding/unbind", headers=auth(anm)).status_code == 403
    assert c.post(f"{API}/admin/users/{p2_user}/patient-binding/unbind", headers=auth(admin)).json() == {"unbound": True}
    assert read(c, p2, cid).status_code == 404


def test_bindings_and_codes_are_append_only_and_audited_without_the_code(world):
    c, anm, cid, p1, _ = world
    code = issue(c, anm, cid).json()["binding_code"]
    bind(c, p1, code)
    bind(c, p1, "SB-ZZZZ-ZZZZ")
    with sqlite3.connect(get_settings().database_path) as db:
        for sql in ("UPDATE patient_identity_events SET patient_id = 'x'", "DELETE FROM patient_identity_events",
                    "DELETE FROM patient_binding_codes", "UPDATE patient_binding_codes SET used_at = NULL, used_by = NULL"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    rows = _rows("SELECT action, outcome, details_json FROM audit_log WHERE action LIKE 'patient_%' AND action != 'patient_registered'")
    results = [(r["action"], json.loads(r["details_json"])["result"]) for r in rows]
    assert ("patient_binding_code_issued", "issued") in results and ("patient_identity_bound", "bound") in results
    assert ("patient_identity_bound", "code_invalid") in results
    blob = json.dumps(_rows("SELECT * FROM audit_log")) + json.dumps(_rows("SELECT * FROM patient_binding_codes"))
    assert code not in blob and code[3:] not in blob and "patient-1" not in json.dumps(_rows("SELECT * FROM audit_log"))
