"""Facility isolation from trusted server config (ACCOUNT_FACILITIES; docs/17 §8). Synthetic data only.

The scope is looked up by the token's username in server settings at every request, never from token claims,
headers, query or body. Tests re-map by changing the environment and clearing the settings cache: the next
request computes the principal again.
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import Role, create_access_token
from app.config import get_settings
from tests.privacy.helpers import auth, grant, token_for, triage

API = "/api/v1"
A = "PHC-KHURDA-01"
B = "PHC-PURI-02"
RED = {"vitals": {"resp_rate": 30}}
# anm_demo works in both facilities so the tests can seed cases in each; everyone else is scoped.
MAPPING = f"anm_demo={A},{B};mo_demo={A};supervisor_demo=*;patient_demo={A}"


def remap(monkeypatch, value: str) -> None:
    monkeypatch.setenv("ACCOUNT_FACILITIES", value)
    get_settings.cache_clear()


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """App with isolation on, the fake AI provider, voice (no engines) and OCR (no engines) enabled, so every case
    endpoint family reaches its case-access check."""
    for k, v in {
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "JWT_SECRET_KEY": "test-secret-key-with-enough-length-for-hs256",
        "ENVIRONMENT": "test",
        "AZURE_OPENAI_API_KEY": "",
        "AZURE_OPENAI_ENDPOINT": "",
        "AI_PROVIDER": "fake",
        "VOICE_ENABLED": "1",
        "VOICE_CLOUD_STT_ENABLED": "0",
        "VOICE_TTS_ENABLED": "0",
        "VOICE_LOCAL_ASR_ENABLED": "0",
        "OCR_ENABLED": "1",
        "OCR_RETENTION_DAYS": "none",
        "OCR_DOCUMENT_DIR": str(tmp_path / "documents"),
        "OCR_RXNORM_DB": "/nonexistent",
        "ACCOUNT_FACILITIES": MAPPING,
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def seed(client, anm: str, facility: str, inputs: dict | None = None) -> tuple[str, str]:
    r = client.post(f"{API}/cases", json={"scenario": "opd", "facility_code": facility}, headers=auth(anm))
    assert r.status_code == 201, r.text
    cid = r.json()["case_id"]
    assert grant(client, anm, cid, ai=True).status_code == 200
    r = triage(client, anm, cid, **(inputs or {}))
    assert r.status_code == 200, r.text
    return cid, r.json()["run_id"]


def rows(sql: str, *args) -> list:
    with sqlite3.connect(get_settings().database_path) as c:
        return c.execute(sql, args).fetchall()


def case_requests(cid: str, run_id: str, triage_body: dict) -> list[tuple[str, str, dict | None]]:
    fid = str(uuid.uuid4())
    return [
        ("GET", f"/cases/{cid}", None),
        ("GET", f"/cases/{cid}/consent", None),
        ("POST", f"/cases/{cid}/triage", triage_body),
        ("GET", f"/cases/{cid}/triage/runs/{run_id}/counterfactuals", None),
        ("GET", f"/triage/{cid}", None),
        ("PATCH", f"/triage/{cid}/sign-off", {"triage_run_id": run_id, "confirm": True}),
        ("PATCH", f"/triage/{cid}/override", {"triage_run_id": run_id, "new_urgency": "RED", "reason_code": "clinical_reassessment", "confirm": True}),
        ("POST", f"/triage/{cid}/corrections", {"expected_triage_run_id": run_id, "input": triage_body, "reason_code": "remeasured", "confirm": True}),
        ("POST", f"/triage/{cid}/acknowledge", {"triage_run_id": run_id}),
        ("POST", f"/cases/{cid}/ai/extractions", {"idempotency_key": str(uuid.uuid4()), "intake_text": "fever three days"}),
        ("GET", f"/cases/{cid}/ai/extractions", None),
        ("GET", f"/cases/{cid}/ai/reviewed", None),
        ("GET", f"/cases/{cid}/ai/notes", None),
        ("POST", f"/cases/{cid}/ai/fields/{fid}/review", {"outcome": "accepted"}),
        ("GET", f"/cases/{cid}/voice/transcriptions", None),
        ("GET", f"/cases/{cid}/voice/prefill", None),
        ("GET", f"/cases/{cid}/documents", None),
        ("GET", f"/cases/{cid}/documents/reviewed", None),
    ]


def call(client, tok, method, path, body, headers=None):
    return client.request(method, API + path, json=body, headers={**auth(tok), **(headers or {})})


def _err(r) -> tuple[int, str | None, str | None]:
    e = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code, e.get("code"), e.get("message")


def test_mo_cannot_touch_another_facilitys_case_and_it_looks_missing(iso):
    anm, mo = token_for(iso, "anm"), token_for(iso, "mo")
    cid_b, run_b = seed(iso, anm, B, RED)
    body = iso.get(f"{API}/triage/{seed(iso, anm, A)[0]}", headers=auth(mo)).json()["latest"]["input"]
    missing, missing_run = str(uuid.uuid4()), str(uuid.uuid4())
    before = rows("SELECT COUNT(*) FROM review_events")[0][0], rows("SELECT COUNT(*) FROM triage_runs")[0][0], rows("SELECT COUNT(*) FROM ai_extraction_runs")[0][0]
    for (method, path, b), (_, mpath, mb) in zip(case_requests(cid_b, run_b, body), case_requests(missing, missing_run, body)):
        got, want = _err(call(iso, mo, method, path, b)), _err(call(iso, mo, method, mpath, mb))
        assert got == want == (404, "NOT_FOUND", "Resource not found"), (method, path, got, want)
    after = rows("SELECT COUNT(*) FROM review_events")[0][0], rows("SELECT COUNT(*) FROM triage_runs")[0][0], rows("SELECT COUNT(*) FROM ai_extraction_runs")[0][0]
    assert before == after  # nothing written for the other facility's case


def test_mo_can_work_on_own_facility_case(iso):
    anm, mo = token_for(iso, "anm"), token_for(iso, "mo")
    cid, run = seed(iso, anm, A, RED)
    assert iso.get(f"{API}/cases/{cid}", headers=auth(mo)).status_code == 200
    assert iso.get(f"{API}/triage/{cid}", headers=auth(mo)).status_code == 200
    assert iso.get(f"{API}/cases/{cid}/triage/runs/{run}/counterfactuals", headers=auth(mo)).status_code == 200
    assert iso.get(f"{API}/cases/{cid}/ai/extractions", headers=auth(mo)).status_code == 200
    assert iso.get(f"{API}/cases/{cid}/voice/transcriptions", headers=auth(mo)).status_code == 200
    assert iso.get(f"{API}/cases/{cid}/documents", headers=auth(mo)).status_code == 200
    assert iso.post(f"{API}/triage/{cid}/acknowledge", json={"triage_run_id": run}, headers=auth(mo)).status_code == 200
    r = iso.patch(f"{API}/triage/{cid}/sign-off", json={"triage_run_id": run, "confirm": True}, headers=auth(mo))
    assert r.status_code == 200, r.text


def queue_ids(client, tok, **params) -> set[str]:
    r = client.get(f"{API}/triage/queue", params={"include_signed_off": True, **params}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return {i["case_id"] for i in r.json()["items"]}


def test_queue_and_escalations_are_scoped_and_filter_never_widens(iso):
    anm, mo, sup = token_for(iso, "anm"), token_for(iso, "mo"), token_for(iso, "supervisor")
    a, _ = seed(iso, anm, A, RED)
    b, _ = seed(iso, anm, B, RED)
    assert queue_ids(iso, mo) == {a}
    assert queue_ids(iso, mo, facility_code=B) == set()  # out-of-scope filter: empty, not an error
    assert queue_ids(iso, mo, facility_code=A) == {a}
    esc = iso.get(f"{API}/triage/escalations", params={"facility_code": B}, headers=auth(mo)).json()
    assert esc["items"] == []
    assert {i["case_id"] for i in iso.get(f"{API}/triage/escalations", headers=auth(mo)).json()["items"]} == {a}
    assert queue_ids(iso, sup) == {a, b}  # "*" = all facilities
    assert queue_ids(iso, sup, facility_code=B) == {b}


def test_supervisor_scoped_to_a_sees_only_a_in_queue_governance_and_audit(iso, monkeypatch):
    anm, mo = token_for(iso, "anm"), token_for(iso, "mo")
    a, run_a = seed(iso, anm, A, RED)
    b, run_b = seed(iso, anm, B, RED)
    b2, run_b2 = seed(iso, anm, B)
    sup = token_for(iso, "supervisor")
    every = iso.get(f"{API}/audit/governance", headers=auth(sup)).json()
    assert every["sample_size"] == 3 and every["facility_scope"] is None and every["red_escalation"]["red_cases"] == 2
    remap(monkeypatch, f"anm_demo={A},{B};mo_demo={A};supervisor_demo={A}")
    assert queue_ids(iso, sup) == {a}
    gov = iso.get(f"{API}/audit/governance", headers=auth(sup)).json()
    assert gov["sample_size"] == 1 and gov["facility_scope"] == [A] and gov["red_escalation"]["red_cases"] == 1
    assert gov["review_completion"]["denominator"] == 1
    assert iso.get(f"{API}/audit/{a}", headers=auth(sup)).status_code == 200
    assert _err(iso.get(f"{API}/audit/{b}", headers=auth(sup))) == _err(iso.get(f"{API}/audit/{uuid.uuid4()}", headers=auth(sup)))
    assert iso.get(f"{API}/audit/{b}", headers=auth(sup)).status_code == 404
    assert iso.get(f"{API}/triage/{b}", headers=auth(sup)).status_code == 404
    assert iso.post(f"{API}/audit/verify", headers=auth(sup)).json()["ok"] is True  # global by design (no case content)


def test_governance_ai_review_counts_are_scoped(iso, monkeypatch):
    anm, mo, sup = token_for(iso, "anm"), token_for(iso, "mo"), token_for(iso, "supervisor")
    b, _ = seed(iso, anm, B)
    remap(monkeypatch, f"anm_demo={A},{B};mo_demo={A},{B};supervisor_demo=*")
    r = iso.post(f"{API}/cases/{b}/ai/extractions", json={"idempotency_key": str(uuid.uuid4()), "intake_text": "fever for three days, temperature 102 F"}, headers=auth(mo))
    assert r.status_code == 201, r.text
    fields = [f for f in r.json().get("fields", []) if f.get("field_id")]
    if not fields:
        pytest.skip("fake extractor returned no reviewable field for this text")
    rv = iso.post(f"{API}/cases/{b}/ai/fields/{fields[0]['field_id']}/review", json={"outcome": "rejected"}, headers=auth(mo))
    assert rv.status_code == 200, rv.text
    assert iso.get(f"{API}/audit/governance", headers=auth(sup)).json()["ai_field_reviews"]["total"] == 1
    remap(monkeypatch, f"anm_demo={A},{B};mo_demo={A},{B};supervisor_demo={A}")
    assert iso.get(f"{API}/audit/governance", headers=auth(sup)).json()["ai_field_reviews"]["total"] == 0


def test_anm_cannot_create_a_case_in_another_facility(iso, monkeypatch):
    remap(monkeypatch, f"anm_demo={A};mo_demo={A};supervisor_demo=*;patient_demo={A}")
    anm, patient = token_for(iso, "anm"), token_for(iso, "patient")
    n_cases, n_audit = rows("SELECT COUNT(*) FROM cases")[0][0], rows("SELECT COUNT(*) FROM audit_log")[0][0]
    for tok in (anm, patient):
        r = iso.post(f"{API}/cases", json={"scenario": "opd", "facility_code": B}, headers=auth(tok))
        assert _err(r) == (403, "FORBIDDEN", "facility not permitted for this account")
    assert rows("SELECT COUNT(*) FROM cases")[0][0] == n_cases
    assert rows("SELECT COUNT(*) FROM audit_log")[0][0] == n_audit
    assert iso.post(f"{API}/cases", json={"scenario": "opd", "facility_code": A}, headers=auth(anm)).status_code == 201


def test_unmapped_account_under_an_active_mapping_gets_nothing(iso, monkeypatch):
    anm = token_for(iso, "anm")
    a, _ = seed(iso, anm, A, RED)
    remap(monkeypatch, f"anm_demo={A},{B};supervisor_demo=*")  # mo_demo not mapped
    mo = token_for(iso, "mo")
    assert queue_ids(iso, mo) == set()
    assert iso.get(f"{API}/triage/{a}", headers=auth(mo)).status_code == 404
    me = iso.get(f"{API}/auth/me", headers=auth(mo)).json()
    assert me["facility_scope"] == [] and me["facility_isolation"] == "enforced"
    other = create_access_token("anm_other", Role.ANM)[0]  # an account the mapping does not know
    assert iso.post(f"{API}/cases", json={"scenario": "opd", "facility_code": A}, headers=auth(other)).status_code == 403


def test_scope_cannot_be_widened_by_headers_query_body_or_token_claims(iso):
    import jwt

    anm, mo = token_for(iso, "anm"), token_for(iso, "mo")
    b, run_b = seed(iso, anm, B, RED)
    spoof = {"X-SEHAT-Facility": B, "X-SEHAT-Facility-Code": B, "X-SEHAT-Facilities": "*", "X-Facility-Code": B}
    assert iso.get(f"{API}/triage/{b}", headers={**auth(mo), **spoof}).status_code == 404
    assert queue_ids(iso, mo, facility_code=B) == set()
    r = iso.get(f"{API}/triage/queue", params={"facility_code": B, "facilities": "*", "scope": B}, headers={**auth(mo), **spoof})
    assert r.status_code == 200 and r.json()["items"] == []
    r = iso.patch(f"{API}/triage/{b}/sign-off", json={"triage_run_id": run_b, "confirm": True, "facility_code": B}, headers=auth(mo))
    assert r.status_code in (400, 404)  # extra body fields are rejected; never a sign-off
    assert rows("SELECT COUNT(*) FROM review_events")[0][0] == 0
    # A correctly signed token carrying extra scope claims is ignored: scope comes from server config only.
    s = get_settings()
    claims = jwt.decode(mo, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    widened = jwt.encode({**claims, "facilities": ["*"], "facility_code": B, "facility_scope": None}, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    assert iso.get(f"{API}/triage/{b}", headers=auth(widened)).status_code == 404
    assert iso.get(f"{API}/auth/me", headers=auth(widened)).json()["facility_scope"] == [A]
    # A username swapped onto another subject (to borrow the supervisor's "*") is refused outright.
    swapped = jwt.encode({**claims, "username": "supervisor_demo"}, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    assert iso.get(f"{API}/auth/me", headers=auth(swapped)).status_code == 401


def test_auth_me_reports_scope(iso, monkeypatch):
    me = lambda who: iso.get(f"{API}/auth/me", headers=auth(token_for(iso, who))).json()  # noqa: E731
    assert (me("mo")["facility_scope"], me("mo")["facility_isolation"]) == ([A], "enforced")
    assert (me("supervisor")["facility_scope"], me("supervisor")["facility_isolation"]) == (None, "enforced")
    assert me("anm")["facility_scope"] == sorted([A, B])
    remap(monkeypatch, "")
    assert (me("mo")["facility_scope"], me("mo")["facility_isolation"]) == (None, "off")


def test_isolation_off_keeps_the_earlier_behaviour(iso, monkeypatch):
    remap(monkeypatch, "")
    anm, mo = token_for(iso, "anm"), token_for(iso, "mo")
    b, _ = seed(iso, anm, B)
    assert iso.get(f"{API}/triage/{b}", headers=auth(mo)).status_code == 200
    assert b in queue_ids(iso, mo)


def test_audit_case_read_404s_for_a_missing_case_even_with_isolation_off(iso, monkeypatch):
    remap(monkeypatch, "")
    sup = token_for(iso, "supervisor")
    assert _err(iso.get(f"{API}/audit/{uuid.uuid4()}", headers=auth(sup))) == (404, "NOT_FOUND", "Resource not found")
    a, _ = seed(iso, token_for(iso, "anm"), A)
    r = iso.get(f"{API}/audit/{a}", headers=auth(sup))
    assert r.status_code == 200 and r.json()["events"]


@pytest.mark.parametrize("value", [
    "mo_demo",                          # no '='
    "mo_demo=phc-a",                    # lower case
    "mo_demo=AB",                       # too short
    f"mo_demo={A},*",                   # '*' mixed with codes
    f"mo_demo={A};mo_demo={B}",         # duplicate account
    f"ghost_user={A}",                  # unknown account
    "=PHC-A1",                          # empty username
    f"mo_demo={A},",                    # empty code
    ";;",                               # only separators
])
def test_malformed_mapping_refuses_to_start_without_echoing_values(monkeypatch, value):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("ACCOUNT_FACILITIES", value)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="ACCOUNT_FACILITIES") as exc:
            get_settings()
        for secretish in ("ghost_user", "phc-a", "PHC-PURI-02"):
            assert secretish not in str(exc.value)
    finally:
        get_settings.cache_clear()


def test_valid_mapping_parses(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("ACCOUNT_FACILITIES", f" anm_demo = {A} , {B} ; supervisor_demo=* ")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.facility_isolation and s.account_facilities == {"anm_demo": frozenset({A, B}), "supervisor_demo": None}
    finally:
        get_settings.cache_clear()
