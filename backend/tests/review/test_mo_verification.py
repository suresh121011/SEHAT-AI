"""Medical officer verification of document values (docs/implementation/mo-verification.md). The medical officer uses the
SAME endpoints as the ANM; access comes from consent.load_case(..., "triage") and the server-side facility mapping
(ACCOUNT_FACILITIES). Synthetic data only; OCR engines replayed."""

import json
import sqlite3
import uuid

import pytest

from app.config import get_settings
from tests.ocr.test_api import _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.ocr.test_lab_flag_end_to_end import KftEngines
from tests.privacy.helpers import auth, grant, token_for

API = "/api/v1"
A, B = "PHC-KHURDA-01", "PHC-PURI-02"


@pytest.fixture
def scoped(ocr_client, monkeypatch):  # noqa: F811
    """anm_demo works in A and B (to seed both); mo_demo only in A."""
    monkeypatch.setenv("ACCOUNT_FACILITIES", f"anm_demo={A},{B};mo_demo={A};supervisor_demo=*;patient_demo={A}")
    get_settings.cache_clear()
    ocr_client.app.state.ocr_engines = KftEngines()
    return ocr_client


def _case_with_doc(c, anm, facility):
    r = c.post(f"{API}/cases", json={"scenario": "opd", "facility_code": facility}, headers=auth(anm))
    assert r.status_code == 201, r.text
    cid = r.json()["case_id"]
    assert grant(c, anm, cid).status_code == 200
    doc = _upload(c, anm, cid).json()
    return cid, doc


def _db(sql, *args):
    with sqlite3.connect(get_settings().database_path) as db:
        return db.execute(sql, args).fetchall()


def test_mo_in_facility_sees_the_queue_and_documents_and_can_match_and_decide(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    cid, doc = _case_with_doc(c, anm, A)
    assert c.get(f"{API}/cases/{cid}/verification-queue", headers=auth(mo)).status_code == 200
    docs = c.get(f"{API}/cases/{cid}/documents", headers=auth(mo)).json()["documents"]
    assert [d["document_id"] for d in docs] == [doc["document_id"]]
    assert c.get(f"{API}/cases/{cid}/documents/{doc['document_id']}/pages/0/image", headers=auth(mo)).status_code == 200

    assert _attest(c, mo, cid, doc["document_id"]).status_code == 200
    rows = {f["analyte_key"]: f for f in doc["fields"]}
    assert _review(c, mo, cid, rows["urea"], "confirmed").status_code == 200
    assert _review(c, mo, cid, rows["creatinine"], "unsure").status_code == 200
    assert _review(c, mo, cid, rows["calcium"], "corrected", {"result": {"value": "9.1"}}).status_code == 200

    # same events as the ANM workflow, attributed to the medical officer
    events = _db("SELECT f.payload_json, e.outcome, e.actor_role, e.corrected_json FROM ocr_review_events e JOIN ocr_fields f USING (field_id) WHERE e.case_id = ?", cid)
    by_name = {json.loads(p)["analyte_key"]: (o, r, cj) for p, o, r, cj in events}
    assert {k: v[:2] for k, v in by_name.items()} == {"urea": ("confirmed", "medical_officer"), "creatinine": ("unsure", "medical_officer"),
                                                       "calcium": ("corrected", "medical_officer")}
    assert json.loads(by_name["calcium"][2])["result"]["value"] == "9.1"  # the correction is recorded separately...
    stored = {json.loads(p)["analyte_key"]: json.loads(p) for (p,) in _db("SELECT payload_json FROM ocr_fields WHERE case_id = ?", cid)}
    assert stored["calcium"]["value"]["raw"] == "8.9"  # ...and the machine reading is kept unchanged
    assert _db("SELECT actor_role FROM ocr_attestation_events WHERE case_id = ?", cid) == [("medical_officer",)]
    audit = _db("SELECT action, actor_role FROM audit_log WHERE case_id = ? AND action IN ('ocr_review_resolved', 'ocr_attestation_recorded')", cid)
    assert sorted(audit) == sorted([("ocr_attestation_recorded", "medical_officer")] + [("ocr_review_resolved", "medical_officer")] * 3)


def test_mo_decision_shows_in_the_queue_exactly_as_an_anm_decision(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    cid, doc = _case_with_doc(c, anm, A)
    assert _attest(c, mo, cid, doc["document_id"]).status_code == 200
    urea = next(f for f in doc["fields"] if f["analyte_key"] == "urea")
    assert _review(c, mo, cid, urea, "confirmed").status_code == 200
    for who in (anm, mo):  # one shared record: both roles see the same remaining items
        labels = [i["label"] for i in c.get(f"{API}/cases/{cid}/verification-queue", headers=auth(who)).json()["items"]]
        assert labels == ["SERUM CREATININE", "SERUM CALCIUM"]


def test_mo_cannot_reach_another_facilitys_documents_and_it_looks_missing(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    cid, doc = _case_with_doc(c, anm, B)
    field = doc["fields"][0]
    missing = str(uuid.uuid4())
    before = _db("SELECT COUNT(*) FROM ocr_review_events")[0][0], _db("SELECT COUNT(*) FROM ocr_attestation_events")[0][0]
    for case in (cid, missing):
        for method, path, body in [
            ("GET", f"/cases/{case}/verification-queue", None),
            ("GET", f"/cases/{case}/documents", None),
            ("GET", f"/cases/{case}/documents/{doc['document_id']}/pages/0/image", None),
            ("POST", f"/cases/{case}/documents/{doc['document_id']}/attestation", {"answer": "matches", "supersedes": None}),
            ("POST", f"/cases/{case}/documents/fields/{field['field_id']}/review",
             {"outcome": "confirmed", "supersedes": None, "shown_png_sha256": field["page_png_sha256"], "shown_regions_sha256": field["regions_sha256"]}),
        ]:
            r = c.request(method, API + path, json=body, headers=auth(mo))
            assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", (case == cid, path, r.status_code)
    assert (_db("SELECT COUNT(*) FROM ocr_review_events")[0][0], _db("SELECT COUNT(*) FROM ocr_attestation_events")[0][0]) == before


def test_substituting_another_cases_field_or_document_into_an_allowed_case_is_refused(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    own, _ = _case_with_doc(c, anm, A)
    _, other_doc = _case_with_doc(c, anm, B)
    field = other_doc["fields"][0]
    r = _review(c, mo, own, field, "confirmed")
    assert r.status_code == 404
    assert _attest(c, mo, own, other_doc["document_id"]).status_code == 404
    assert _db("SELECT COUNT(*) FROM ocr_review_events WHERE field_id = ?", field["field_id"])[0][0] == 0


def test_patient_supervisor_and_anonymous_cannot_verify(scoped):
    c = scoped
    anm = token_for(c, "anm")
    cid, doc = _case_with_doc(c, anm, A)
    field = doc["fields"][0]
    body = {"outcome": "confirmed", "supersedes": None, "shown_png_sha256": field["page_png_sha256"], "shown_regions_sha256": field["regions_sha256"]}
    url = f"{API}/cases/{cid}/documents/fields/{field['field_id']}/review"
    assert c.post(url, json=body, headers=auth(token_for(c, "patient"))).status_code == 403
    assert c.post(url, json=body, headers=auth(token_for(c, "supervisor"))).status_code == 403
    assert c.post(url, json=body).status_code == 401
    assert c.get(f"{API}/cases/{cid}/verification-queue").status_code == 401
    assert _db("SELECT COUNT(*) FROM ocr_review_events WHERE case_id = ?", cid)[0][0] == 0


def test_mo_verification_never_writes_triage_or_changes_urgency(scoped):
    c = scoped
    anm, mo = token_for(c, "anm"), token_for(c, "mo")
    cid, doc = _case_with_doc(c, anm, A)
    assert _attest(c, mo, cid, doc["document_id"]).status_code == 200
    for f in doc["fields"]:
        assert _review(c, mo, cid, f, "confirmed").status_code == 200
    assert _db("SELECT COUNT(*) FROM triage_runs WHERE case_id = ?", cid)[0][0] == 0
    assert _db("SELECT COUNT(*) FROM review_events WHERE case_id = ?", cid)[0][0] == 0  # no sign-off or override
