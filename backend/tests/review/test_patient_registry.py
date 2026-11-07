"""P1 patient registry (docs/implementation/p1-patient-registry.md): a visit is linked to a patient only by a SEHAT
patient code the patient holds, typed and confirmed by a health worker, under `link_visits` consent. No identifiers,
no automatic matching, nothing merged; a wrong link is undone by an append-only unlink. Synthetic data only."""

import json
import sqlite3

import pytest

from app import patient_registry as reg
from app.config import get_settings
from app.consent_notice import NOTICE_VERSION
from tests.privacy.helpers import auth, token_for, withdraw

API = "/api/v1"
A, B = "PHC-KHURDA-01", "PHC-PURI-02"


def _case(c, anm, facility=A, link=True, language="en"):
    r = c.post(f"{API}/cases", json={"scenario": "opd", "facility_code": facility}, headers=auth(anm))
    assert r.status_code == 201, r.text
    cid = r.json()["case_id"]
    r = c.post(f"{API}/cases/{cid}/consent", headers=auth(anm), json={"decision": "grant", "include_link_visits": link, "language": language,
                                                                      "notice_version": NOTICE_VERSION})
    assert r.status_code == 200, r.text
    return cid


def _db(sql, *args):
    with sqlite3.connect(get_settings().database_path) as db:
        return db.execute(sql, args).fetchall()


def _new(c, tok, cid):
    return c.post(f"{API}/cases/{cid}/patient/new", headers=auth(tok))


def _link(c, tok, cid, code):
    return c.post(f"{API}/cases/{cid}/patient/link", headers=auth(tok), json={"patient_code": code, "confirm": True})


@pytest.fixture
def scoped(client, monkeypatch):
    monkeypatch.setenv("ACCOUNT_FACILITIES", f"anm_demo={A},{B};mo_demo={A};supervisor_demo=*;patient_demo={A}")
    get_settings.cache_clear()
    yield client
    get_settings.cache_clear()


# ── the code ────────────────────────────────────────────────────────────────────────────────────────────────

def test_codes_are_well_formed_and_any_single_typo_is_caught():
    for _ in range(200):
        code = reg.new_code()
        assert code.startswith("SP-") and len(code) == 12 and reg.normalize_code(code) == code
        body = code[3:].replace("-", "")  # not str.replace("SP-"): a code body may itself contain "SP-" at the group join
        for i in range(8):
            for ch in reg.ALPHABET:
                if ch != body[i]:
                    typo = body[:i] + ch + body[i + 1:]
                    assert reg.normalize_code(typo) is None  # every single-character substitution is detected


def test_code_entry_tolerates_case_spaces_and_confusable_letters():
    code = reg.new_code()
    plain = code[3:].replace("-", "")
    assert reg.normalize_code(f" sp {plain[:4]} {plain[4:]} ".lower()) == code
    assert reg.normalize_code(plain) == code
    with_o = plain.replace("0", "O")
    assert reg.normalize_code(with_o) == code  # O read as zero, I/L as one (Crockford)


# ── consent ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_linking_needs_link_visits_consent_and_a_refusal_is_audited(client):
    anm = token_for(client, "anm")
    cid = _case(client, anm, link=False)
    r = _new(client, anm, cid)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert _db("SELECT COUNT(*) FROM patients")[0][0] == 0
    assert ("consent_denied",) in _db("SELECT action FROM audit_log WHERE case_id = ?", cid)


def test_link_visits_cannot_be_granted_in_hindi_or_odia_yet(client):
    anm = token_for(client, "anm")
    for lang in ("hi", "or"):
        r = client.post(f"{API}/cases", json={"scenario": "opd", "facility_code": A}, headers=auth(anm))
        cid = r.json()["case_id"]
        r = client.post(f"{API}/cases/{cid}/consent", headers=auth(anm), json={"decision": "grant", "include_link_visits": True, "language": lang,
                                                                             "notice_version": NOTICE_VERSION})
        assert r.status_code == 400 and r.json()["error"]["code"] == "PURPOSE_NOT_AVAILABLE_IN_LANGUAGE"
        assert _db("SELECT COUNT(*) FROM consent_events WHERE case_id = ?", cid)[0][0] == 0  # nothing recorded


# ── register, look up, link ─────────────────────────────────────────────────────────────────────────────────

def test_first_visit_gets_a_code_and_a_return_visit_links_by_that_code(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    v1 = _case(c, anm)
    r = _new(c, anm, v1)
    assert r.status_code == 201
    code = r.json()["patient_code"]
    v2 = _case(c, anm)
    look = c.post(f"{API}/cases/{v2}/patient/lookup", headers=auth(anm), json={"patient_code": code.lower()})
    assert look.status_code == 200 and look.json()["visible_visits"] == 1 and look.json()["already_linked_here"] is False
    assert _link(c, anm, v2, code).status_code == 200
    view = c.get(f"{API}/cases/{v2}/patient", headers=auth(mo)).json()
    assert view["linked"] and view["patient_code"] == code and [v["case_id"] for v in view["other_visits"]] == [v1]
    assert set(view["other_visits"][0]) == {"case_id", "visit_date", "facility_code", "scenario"}  # no clinical content here


def test_no_name_or_identifier_is_stored_and_the_code_never_reaches_the_audit_log(scoped):
    c = scoped
    anm = token_for(c, "anm")
    v1 = _case(c, anm)
    code = _new(c, anm, v1).json()["patient_code"]
    v2 = _case(c, anm)
    assert _link(c, anm, v2, code).status_code == 200
    cols = {r[1] for r in _db("PRAGMA table_info(patients)")}
    assert cols == {"patient_id", "patient_code", "facility_code", "created_by", "created_by_role", "created_at"}
    audit = json.dumps(_db("SELECT action, details_json FROM audit_log WHERE action LIKE 'patient_%'"))
    assert code not in audit and code[3:] not in audit
    assert {a for (a,) in _db("SELECT action FROM audit_log WHERE action LIKE 'patient_%'")} == {"patient_registered", "patient_linked"}


def test_a_mistyped_code_is_rejected_before_any_lookup(scoped):
    c = scoped
    anm = token_for(c, "anm")
    code = _new(c, anm, _case(c, anm)).json()["patient_code"]
    v2 = _case(c, anm)
    typo = code[:-1] + ("0" if code[-1] != "0" else "1")
    r = _link(c, anm, v2, typo)
    assert r.status_code == 400 and r.json()["error"]["code"] == "PATIENT_CODE_INVALID"
    assert not _db("SELECT 1 FROM patient_link_events WHERE case_id = ?", v2)


def test_unknown_and_out_of_scope_codes_look_the_same(scoped, monkeypatch):
    c = scoped
    anm = token_for(c, "anm")
    code_b = _new(c, anm, _case(c, anm, facility=B)).json()["patient_code"]  # registered at facility B
    unknown = reg.new_code()
    v = _case(c, anm, facility=A)
    monkeypatch.setenv("ACCOUNT_FACILITIES", f"anm_demo={A};mo_demo={A};supervisor_demo=*;patient_demo={A}")  # now A only
    get_settings.cache_clear()
    for code in (code_b, unknown):
        r = _link(c, anm, v, code)
        assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND")
        r = c.post(f"{API}/cases/{v}/patient/lookup", headers=auth(anm), json={"patient_code": code})
        assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert not _db("SELECT 1 FROM patient_link_events WHERE case_id = ?", v)


def test_a_visit_has_one_patient_and_relinking_elsewhere_needs_an_unlink_first(scoped):
    c = scoped
    anm = token_for(c, "anm")
    code_1 = _new(c, anm, _case(c, anm)).json()["patient_code"]
    code_2 = _new(c, anm, _case(c, anm)).json()["patient_code"]
    v = _case(c, anm)
    assert _link(c, anm, v, code_1).status_code == 200
    assert _link(c, anm, v, code_1).json().get("unchanged") is True  # repeating the same link is harmless
    r = _link(c, anm, v, code_2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "VISIT_ALREADY_LINKED"
    assert _new(c, anm, v).status_code == 409


def test_a_wrong_link_is_undone_by_an_unlink_and_both_events_are_kept(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    v1 = _case(c, anm)
    code = _new(c, anm, v1).json()["patient_code"]
    v2 = _case(c, anm)
    assert _link(c, anm, v2, code).status_code == 200
    r = c.post(f"{API}/cases/{v2}/patient/unlink", headers=auth(mo), json={"reason_code": "linked_in_error", "confirm": True})
    assert r.status_code == 200 and r.json()["linked"] is False
    assert [a for (a,) in _db("SELECT action FROM patient_link_events WHERE case_id = ? ORDER BY seq", v2)] == ["link", "unlink"]
    assert c.get(f"{API}/cases/{v1}/patient", headers=auth(mo)).json()["other_visits"] == []  # no longer shown together
    assert ("patient_unlinked", "medical_officer") in _db("SELECT action, actor_role FROM audit_log WHERE case_id = ?", v2)
    with sqlite3.connect(get_settings().database_path) as db, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM patient_link_events")


def test_withdrawing_link_visits_hides_that_visit_from_the_others(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    v1 = _case(c, anm)
    code = _new(c, anm, v1).json()["patient_code"]
    v2 = _case(c, anm)
    assert _link(c, anm, v2, code).status_code == 200
    assert withdraw(c, anm, v1, "link_visits").status_code == 200
    assert c.get(f"{API}/cases/{v2}/patient", headers=auth(mo)).json()["other_visits"] == []  # v1 no longer shown
    own = c.get(f"{API}/cases/{v1}/patient", headers=auth(mo)).json()
    assert own["other_visits"] == [] and own["other_visits_hidden_reason"] == "link_visits_consent_not_in_effect"


def test_other_visits_are_only_those_this_account_may_read(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")  # mo_demo works in A only
    v_b = _case(c, anm, facility=B)
    code = _new(c, anm, v_b).json()["patient_code"]
    v_a = _case(c, anm, facility=A)
    assert _link(c, anm, v_a, code).status_code == 200  # anm_demo covers both facilities
    assert c.get(f"{API}/cases/{v_a}/patient", headers=auth(mo)).json()["other_visits"] == []  # the B visit is not shown to an A-only doctor
    assert [v["case_id"] for v in c.get(f"{API}/cases/{v_a}/patient", headers=auth(anm)).json()["other_visits"]] == [v_b]


def test_roles_and_scope_on_every_registry_endpoint(scoped):
    c = scoped
    anm = token_for(c, "anm")
    cid = _case(c, anm)
    code = _new(c, anm, cid).json()["patient_code"]
    other = _case(c, anm)
    for who, expected in (("patient", 403), ("supervisor", 403)):
        tok = token_for(c, who)
        assert _link(c, tok, other, code).status_code == expected
        assert c.get(f"{API}/cases/{cid}/patient", headers=auth(tok)).status_code == expected
    assert c.post(f"{API}/cases/{other}/patient/new").status_code == 401
    mo = token_for(c, "mo")
    assert _new(c, mo, other).status_code == 403  # a doctor views and may undo links, but issuing and linking is the ANM's step
    b_case = _case(c, anm, facility=B)
    r = c.get(f"{API}/cases/{b_case}/patient", headers=auth(mo))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND")  # another facility's visit looks missing


def test_linking_never_writes_triage_or_review(scoped):
    c = scoped
    anm = token_for(c, "anm")
    v1 = _case(c, anm)
    code = _new(c, anm, v1).json()["patient_code"]
    v2 = _case(c, anm)
    assert _link(c, anm, v2, code).status_code == 200
    assert _db("SELECT COUNT(*) FROM triage_runs")[0][0] == 0 and _db("SELECT COUNT(*) FROM review_events")[0][0] == 0
