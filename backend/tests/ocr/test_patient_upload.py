"""Patient self-upload of text documents (docs/14 §4A, docs/11 §3a). Synthetic documents only.

Distinct synthetic principals (`patient_other`, `anm_other`) exercise the per-account checks in code; every
`patient_demo` session shares one account, so the demo cannot separate real patients (docs/11 limitation).
"""

import sqlite3

from app.config import get_settings
from app.ocr import service
from tests.ocr.test_api import _review, _upload, ocr_client  # noqa: F401 - fixture re-export
from tests.privacy.helpers import audit_rows, auth, grant, new_case, token_for, withdraw


def _patient_case(client):
    pat = token_for(client, "patient")
    cid = new_case(client, pat)
    assert grant(client, pat, cid).status_code == 200
    return pat, cid


def _doc_rows(case_id: str) -> int:
    with sqlite3.connect(get_settings().database_path) as c:
        return c.execute("SELECT COUNT(*) FROM ocr_documents WHERE case_id = ?", (case_id,)).fetchone()[0]


def test_patient_uploads_a_lab_report_to_own_case_and_it_is_read_but_not_confirmed(ocr_client):
    pat, cid = _patient_case(ocr_client)
    resp = _upload(ocr_client, pat, cid)
    assert resp.status_code in (200, 201), resp.text
    doc = resp.json()
    assert doc["status"] == "completed" and doc["fields"]
    assert all(f["review_status"] == "machine_read" for f in doc["fields"])  # nobody has confirmed anything
    started = [r for r in audit_rows(ocr_client) if r["action"] == "ocr_document_started" and r["case_id"] == cid]
    assert len(started) == 1 and started[0]["actor_role"] == "patient"
    assert ocr_client.get(f"/api/v1/cases/{cid}/documents", headers=auth(pat)).status_code == 200


def test_patient_cannot_upload_to_another_accounts_case(ocr_client):
    _, cid = _patient_case(ocr_client)
    assert _upload(ocr_client, token_for(ocr_client, "patient_other"), cid).status_code == 404
    anm = token_for(ocr_client, "anm")
    anm_case = new_case(ocr_client, anm)
    grant(ocr_client, anm, anm_case)
    assert _upload(ocr_client, token_for(ocr_client, "patient"), anm_case).status_code == 404
    assert _doc_rows(cid) == 0 and _doc_rows(anm_case) == 0


def test_patient_cannot_upload_medical_images(ocr_client):
    pat, cid = _patient_case(ocr_client)
    for image_type in ("chest_xray", "ecg_strip", "wound_photo"):
        resp = _upload(ocr_client, pat, cid, doc_type=image_type)
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "FORBIDDEN"
    with sqlite3.connect(get_settings().database_path) as c:
        assert c.execute("SELECT COUNT(*) FROM medical_images WHERE case_id = ?", (cid,)).fetchone()[0] == 0


def test_patient_cannot_review_attest_delete_or_read_reviewed_values(ocr_client):
    pat, cid = _patient_case(ocr_client)
    doc = _upload(ocr_client, pat, cid).json()
    base = f"/api/v1/cases/{cid}/documents"
    assert _review(ocr_client, pat, cid, doc["fields"][0], "confirmed").status_code == 403
    assert ocr_client.post(f"{base}/{doc['document_id']}/attestation", json={"answer": "matches", "supersedes": None}, headers=auth(pat)).status_code == 403
    assert ocr_client.delete(f"{base}/{doc['document_id']}", headers=auth(pat)).status_code == 403
    assert ocr_client.get(f"{base}/reviewed", headers=auth(pat)).status_code == 403


def test_anm_reviews_a_patient_upload_after_taking_over_the_case(ocr_client):
    pat, cid = _patient_case(ocr_client)
    doc = _upload(ocr_client, pat, cid).json()
    anm = token_for(ocr_client, "anm")
    assert _review(ocr_client, anm, cid, doc["fields"][0], "confirmed").status_code == 404  # not taken over yet
    code = ocr_client.get(f"/api/v1/cases/{cid}", headers=auth(pat)).json()["patient_token"]
    assert ocr_client.post("/api/v1/cases/handover", json={"patient_token": code}, headers=auth(anm)).status_code == 200
    att = ocr_client.post(f"/api/v1/cases/{cid}/documents/{doc['document_id']}/attestation", json={"answer": "matches", "supersedes": None}, headers=auth(anm))
    assert att.status_code == 200, att.text
    assert _review(ocr_client, anm, cid, doc["fields"][0], "unsure").status_code == 200
    # The handling ANM can still upload too.
    assert _upload(ocr_client, anm, cid).status_code in (200, 201)


def test_anm_upload_on_own_case_still_works(ocr_client):
    anm = token_for(ocr_client, "anm")
    cid = new_case(ocr_client, anm)
    grant(ocr_client, anm, cid)
    assert _upload(ocr_client, anm, cid).status_code in (200, 201)
    assert _upload(ocr_client, token_for(ocr_client, "anm_other"), cid).status_code == 404


def test_patient_upload_needs_consent_and_authentication(ocr_client):
    pat = token_for(ocr_client, "patient")
    cid = new_case(ocr_client, pat)
    assert _upload(ocr_client, pat, cid).status_code == 403  # no consent yet
    grant(ocr_client, pat, cid)
    withdraw(ocr_client, pat, cid, "triage")
    assert _upload(ocr_client, pat, cid).status_code == 403
    assert _doc_rows(cid) == 0
    resp = ocr_client.post("/api/v1/intake/document", data={"case_id": cid, "document_type": "lab_report", "idempotency_key": "x"},
                           files={"file": ("a.png", b"x", "image/png")})
    assert resp.status_code == 401


def test_patient_uploads_are_capped_per_case(ocr_client, monkeypatch):
    monkeypatch.setattr(service, "PATIENT_MAX_DOCUMENTS_PER_CASE", 1)
    pat, cid = _patient_case(ocr_client)
    assert _upload(ocr_client, pat, cid).status_code in (200, 201)
    resp = _upload(ocr_client, pat, cid)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "UPLOAD_LIMIT_REACHED"
    assert _doc_rows(cid) == 1
