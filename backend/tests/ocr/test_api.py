"""Document OCR API end to end with engines faked (docs/14 §8). Label: `tested_mock` for the API path.
PaddleOCR is replayed from fixture ground truth; Surya and Chandra replay outputs recorded from the real
local engines on 2026-10-01 (tests/fixtures/ocr/engine_outputs). Synthetic documents only."""

import json
import logging
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.config import get_settings
from app.ocr.types import Line, PageOCR, Word
from tests.privacy.helpers import audit_rows, auth, grant, new_case, token_for, withdraw

FIX = Path(__file__).parents[1] / "fixtures" / "ocr"
OUT = FIX / "engine_outputs"
CANARY = ("Zzyzx", "Canary-Testpatient", "90000 00042")


class FakeEngines:
    """Same interface as app.ocr.service.Engines; deterministic and offline."""

    def __init__(self, fixture: str, *, paddle_edit=None, reread=None, fail=None):
        self.fixture, self.paddle_edit, self.reread_text, self.fail = fixture, paddle_edit or {}, reread, fail
        self.calls: list[str] = []

    def paddle_page(self, png, page_index):
        self.calls.append("paddle")
        if self.fail == "paddle":
            from app.ocr.engine import EngineError

            raise EngineError("inference_failed", 500)
        truth = json.loads((FIX / f"{self.fixture}.truth.json").read_text())["pages"][page_index]
        lines = []
        for i, d in enumerate(truth["drawn"]):
            text = self.paddle_edit.get(d["text"], d["text"])
            box = tuple(d["bbox"])
            score = 0.62 if text in self.paddle_edit.get("_low", ()) else 0.99
            lines.append(Line(f"p{page_index}l{i}", text, box, score, (Word(text, box, score),)))
        return PageOCR(page_index, truth["width"], truth["height"], lines)

    def paddle_reread(self, png, bbox, zoom):
        self.calls.append("reread")
        return (self.reread_text, 0.8) if self.reread_text else None

    def surya_page(self, png):
        self.calls.append("surya")
        if self.fail == "surya":
            from app.ocr.engine import EngineError

            raise EngineError("ocr_timeout", 504)
        data = json.loads((OUT / "surya_cbc_low_platelet.json").read_text())
        return {"blocks": next(iter(data.values()))[0]["blocks"]}

    def chandra_page(self, png):
        self.calls.append("chandra")
        return json.loads((OUT / "chandra_rx_handwritten.json").read_text())


@pytest.fixture
def ocr_client(client, monkeypatch, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    from app.ocr.model_pins import MANIFEST_NAME, MODELS

    for _, (name, _, _) in MODELS.items():
        (models / name).write_bytes(b"x")
    (models / MANIFEST_NAME).write_text("{}")
    monkeypatch.setenv("OCR_ENABLED", "1")
    monkeypatch.setenv("OCR_RETENTION_DAYS", "none")
    monkeypatch.setenv("OCR_SURYA_ENABLED", "1")
    monkeypatch.setenv("OCR_CHANDRA_ENABLED", "1")
    monkeypatch.setenv("OCR_MODEL_DIR", str(models))
    monkeypatch.setenv("OCR_DOCUMENT_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("OCR_RXNORM_DB", str(_tiny_rxnorm(tmp_path)))
    get_settings.cache_clear()
    client.app.state.ocr_engines = FakeEngines("cbc_low_platelet")
    yield client
    get_settings.cache_clear()


def _tiny_rxnorm(tmp_path) -> Path:
    from app.ocr.rxnorm import load_index, norm

    db = tmp_path / "rx.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE names (rxcui TEXT, str TEXT, tty TEXT, norm TEXT)")
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    for cui, s, tty in [("161", "acetaminophen", "IN"), ("723", "amoxicillin", "IN"), ("40790", "pantoprazole", "IN"),
                        ("308182", "amoxicillin 500 MG Oral Capsule", "SCD"), ("313782", "acetaminophen 650 MG Oral Tablet", "SCD"),
                        ("261257", "pantoprazole 40 MG Delayed Release Oral Tablet", "SCD")]:
        c.execute("INSERT INTO names VALUES (?, ?, ?, ?)", (cui, s, tty, norm(s)))
    c.execute("INSERT INTO meta VALUES ('release', 'test')")
    c.commit()
    c.close()
    load_index.cache_clear()
    return db


def _ready_case(client, anm):
    case_id = new_case(client, anm)
    assert grant(client, anm, case_id).status_code == 200
    return case_id


def _upload(client, token, case_id, fixture="cbc_low_platelet.png", doc_type="lab_report", key=None, data=None):
    body = data if data is not None else (FIX / fixture).read_bytes()
    return client.post("/api/v1/intake/document", headers=auth(token),
                       data={"case_id": case_id, "document_type": doc_type, "idempotency_key": key or str(uuid.uuid4())},
                       files={"file": (fixture, body, "application/octet-stream")})


def _review(client, token, case_id, field, outcome, correction=None, supersedes=None):
    body = {"outcome": outcome, "shown_png_sha256": field["page_png_sha256"], "shown_regions_sha256": field["regions_sha256"],
            "supersedes": supersedes if supersedes is not None else (field["review"]["event_id"] if field["review"] else None)}
    if correction is not None:
        body["correction"] = correction
    return client.post(f"/api/v1/cases/{case_id}/documents/fields/{field['field_id']}/review", json=body, headers=auth(token))


def _attest(client, token, case_id, doc_id, answer="matches", supersedes=None):
    return client.post(f"/api/v1/cases/{case_id}/documents/{doc_id}/attestation", json={"answer": answer, "supersedes": supersedes}, headers=auth(token))


# ── happy path ────────────────────────────────────────────────────────────────────────────────────


def test_lab_report_end_to_end_with_provenance_and_both_engines(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    r = _upload(ocr_client, anm, case_id)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "completed" and doc["engines"] == {"paddleocr": "ok", "surya": "ok"}
    assert doc["dates"]["collected_date"] == "15/09/2026"
    plt = next(f for f in doc["fields"] if f["analyte_key"] == "platelets")
    assert plt["value"]["raw"] == "85,000" and plt["value"]["value"] == "85000" and plt["review_status"] == "machine_read"
    assert {x["role"] for x in plt["regions"]} == {"name", "value", "unit", "range", "flag"}
    assert plt["band"] == "accept" and plt["printed_range_status"] == "below_range"
    assert plt["reference"]["source_id"] == "MEDLINEPLUS_PLT" and plt["reference"]["status"] == "below_range"
    assert {x["engine"] for x in plt["readings"]} == {"paddleocr", "surya-ocr-2"}
    # docs/06 §3.3 shape is present and derived from the same fields
    ev = next(e for e in doc["extracted_values"] if e["field_id"] == plt["field_id"])
    assert ev["out_of_range"] is True and ev["bbox"] == next(x["bbox"] for x in plt["regions"] if x["role"] == "value")
    assert {"platelets_below_range_printed", "platelets_below_range_sourced"} <= set(doc["godel_verification"]["reference_range_flags"])
    assert ev["out_of_range_basis"] == ["printed", "sourced"]
    # page image: authorised, integrity-checked, not cached
    img = ocr_client.get(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}/pages/0/image", headers=auth(anm))
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.headers["cache-control"] == "no-store" and img.headers["x-content-type-options"] == "nosniff"
    import hashlib

    assert hashlib.sha256(img.content).hexdigest() == doc["pages"][0]["png_sha256"]


def test_review_flow_attestation_confirm_correct_and_reviewed_view(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    hb = next(f for f in doc["fields"] if f["analyte_key"] == "hemoglobin")
    assert _review(ocr_client, anm, case_id, hb, "confirmed").json()["error"]["code"] == "ATTESTATION_REQUIRED"
    assert _attest(ocr_client, anm, case_id, doc["document_id"]).status_code == 200
    ok = _review(ocr_client, anm, case_id, hb, "confirmed")
    assert ok.status_code == 200 and ok.json()["review_status"] == "confirmed"
    rbc = next(f for f in doc["fields"] if f["analyte_key"] == "rbc")
    corr = _review(ocr_client, anm, case_id, rbc, "corrected", {"result": {"value": "4.20"}})
    assert corr.status_code == 200 and corr.json()["reviewed_value"]["value"] == "4.20"
    assert corr.json()["value"]["raw"] == "4.10"  # the machine reading is kept, never overwritten
    plt = next(f for f in doc["fields"] if f["analyte_key"] == "platelets")
    _review(ocr_client, anm, case_id, plt, "unsure")
    rv = ocr_client.get(f"/api/v1/cases/{case_id}/documents/reviewed", headers=auth(anm)).json()
    names = {v["name"]: v for v in rv["values"]}
    assert set(names) == {"hemoglobin", "rbc"} and names["rbc"]["source"]["type"] == "ocr_manual_correction"
    assert names["hemoglobin"]["ranges"]["printed_range_status"] == "below_range"
    assert any(u.get("field_id") == plt["field_id"] and u["state"] == "unsure" for u in rv["unresolved"])
    assert "never change triage" in rv["note"]


def test_later_no_attestation_hides_confirmed_rows(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    first = _attest(ocr_client, anm, case_id, doc["document_id"]).json()["attestation"]
    hb = next(f for f in doc["fields"] if f["analyte_key"] == "hemoglobin")
    assert _review(ocr_client, anm, case_id, hb, "confirmed").status_code == 200
    assert _attest(ocr_client, anm, case_id, doc["document_id"], "does_not_match", supersedes=first["event_id"]).status_code == 200
    rv = ocr_client.get(f"/api/v1/cases/{case_id}/documents/reviewed", headers=auth(anm)).json()
    assert rv["values"] == [] and rv["unresolved"][0]["state"] == "attestation_does_not_match"


def test_stale_decision_and_shown_evidence_mismatch(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    hb = next(f for f in doc["fields"] if f["analyte_key"] == "hemoglobin")
    assert _review(ocr_client, anm, case_id, hb, "confirmed").status_code == 200
    assert _review(ocr_client, anm, case_id, hb, "unsure", supersedes=None).json()["error"]["code"] == "STALE_DECISION"
    bad = dict(hb, regions_sha256="0" * 64)
    assert _review(ocr_client, anm, case_id, bad, "unsure").json()["error"]["code"] == "STALE_DECISION"


def test_dispute_cannot_be_confirmed_and_unit_only_correction_is_refused(ocr_client):
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ocr_engines = FakeEngines("cbc_low_platelet", paddle_edit={"85,000": "58,000"})  # engines disagree
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    plt = next(f for f in doc["fields"] if f["analyte_key"] == "platelets")
    assert plt["disputed"] and plt["band"] == "amber" and not plt["can_confirm"]
    assert {x["text"] for x in plt["readings"]} == {"58,000", "85,000"}  # both readings shown
    assert _review(ocr_client, anm, case_id, plt, "confirmed").json()["error"]["code"] == "CORRECTION_REQUIRED"
    assert _review(ocr_client, anm, case_id, plt, "corrected", {"unit": "/cumm"}).json()["error"]["code"] == "CORRECTION_REQUIRED"
    ok = _review(ocr_client, anm, case_id, plt, "corrected", {"result": {"value": "85000"}})
    assert ok.status_code == 200 and ok.json()["readings"][0]["text"] == "58,000"


def test_correction_payload_is_structured_only(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    hb = next(f for f in doc["fields"] if f["analyte_key"] == "hemoglobin")
    for corr in ({"result": {"value": "eleven"}}, {"name": "Hb"}, {"unit": "furlongs"}, {"range": {"kind": "between", "low": "15", "high": "12"}},
                 {"result": {"value": "11", "qualitative": "positive"}}):
        r = _review(ocr_client, anm, case_id, hb, "corrected", corr)
        assert r.status_code == 400, (corr, r.text)


def test_prescription_path_chandra_normalisation_is_a_dispute_and_rxnorm_is_local(ocr_client):
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ocr_engines = FakeEngines("rx_handwritten")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id, "rx_handwritten.png", "prescription").json()
    assert doc["status"] == "completed", doc
    meds = {f["drug_raw"]: f for f in doc["fields"] if f["kind"] == "medication"}
    assert set(meds) == {"Paracetamol", "Amoxicillin", "Pantoprazole", "Ambroxol"}
    amox = meds["Amoxicillin"]  # Chandra wrote "Amoxicillin"; the page (PaddleOCR) says "Amoxycillin"
    assert amox["disputed"] and not amox["can_confirm"]
    assert {r["engine"] for r in amox["readings"]} == {"chandra-ocr-2", "paddleocr"}
    para = meds["Paracetamol"]
    assert para["dosage_pattern"] == "1-0-1" and para["rxnorm"]["status"] == "uncertain" and para["rxnorm"]["reason"] == "inn_synonym"
    assert meds["Pantoprazole"]["rxnorm"]["status"] == "matched" and meds["Pantoprazole"]["rxnorm"]["strength_match"] is True
    assert meds["Ambroxol"]["rxnorm"]["status"] == "unknown"  # not in the tiny test index: honest unknown


# ── gates and failures ────────────────────────────────────────────────────────────────────────────


def test_consent_and_notice_version_gates(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = new_case(ocr_client, anm)
    assert _upload(ocr_client, anm, case_id).json()["error"]["code"] == "CONSENT_REQUIRED"
    assert grant(ocr_client, anm, case_id).status_code == 200
    # consent recorded under an older notice (no document wording) does not authorise uploads
    with sqlite3.connect(get_settings().database_path) as c:
        c.execute("DROP TRIGGER consent_events_no_update")
        c.execute("UPDATE consent_events SET notice_version = '2026-10-01.1' WHERE case_id = ?", (case_id,))
    r = _upload(ocr_client, anm, case_id)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_NOTICE_UPDATE_REQUIRED"
    assert grant(ocr_client, anm, case_id).status_code == 200  # re-consent under the current notice
    assert _upload(ocr_client, anm, case_id).status_code == 200


def test_withdrawal_blocks_reads_and_reconsent_reopens(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    withdraw(ocr_client, anm, case_id, "triage")
    for url in (f"/api/v1/cases/{case_id}/documents", f"/api/v1/cases/{case_id}/documents/{doc['document_id']}/pages/0/image"):
        assert ocr_client.get(url, headers=auth(anm)).status_code == 403
    assert grant(ocr_client, anm, case_id).status_code == 200
    assert ocr_client.get(f"/api/v1/cases/{case_id}/documents", headers=auth(anm)).status_code == 200


def test_consent_withdrawn_during_processing_discards_everything(ocr_client, monkeypatch):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    from app.ocr import service

    real = service.run_pipeline

    def withdraw_midway(*a, **k):
        out = real(*a, **k)
        with sqlite3.connect(get_settings().database_path) as c:
            c.execute("INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
                      "VALUES (?, ?, 'triage', 'withdrawn', '2026-10-01.2', 'en', 'project_draft', 'patient_button', 'x', 'anm', 't')", (str(uuid.uuid4()), case_id))
        return out

    monkeypatch.setattr(service, "run_pipeline", withdraw_midway)
    r = _upload(ocr_client, anm, case_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONSENT_WITHDRAWN"
    with sqlite3.connect(get_settings().database_path) as c:
        assert c.execute("SELECT status, failure_code FROM ocr_documents").fetchone() == ("failed", "consent_changed")
        assert c.execute("SELECT count(*) FROM ocr_pages").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM ocr_fields").fetchone()[0] == 0
    assert not list((Path(get_settings().ocr_document_dir)).rglob("*.png"))


def test_idempotency(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    key = str(uuid.uuid4())
    a = _upload(ocr_client, anm, case_id, key=key).json()
    calls = len(ocr_client.app.state.ocr_engines.calls)
    b = _upload(ocr_client, anm, case_id, key=key).json()
    assert a["document_id"] == b["document_id"] and len(ocr_client.app.state.ocr_engines.calls) == calls  # engines not re-run
    c = _upload(ocr_client, anm, case_id, "cbc_normal.png", key=key)
    assert c.status_code == 409 and c.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_engine_failures_are_explicit_never_silent(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    ocr_client.app.state.ocr_engines = FakeEngines("cbc_low_platelet", fail="surya")
    doc = _upload(ocr_client, anm, case_id).json()
    assert doc["engines"]["surya"] == "failed:ocr_timeout"
    assert all(f["band"] != "accept" for f in doc["fields"])  # single-engine reading is capped at amber
    ocr_client.app.state.ocr_engines = FakeEngines("cbc_low_platelet", fail="paddle")
    r = _upload(ocr_client, anm, case_id)
    assert r.status_code == 500 and r.json()["error"]["code"] == "OCR_UNAVAILABLE"


@pytest.mark.parametrize("data,code", [
    (b"GIF89a" + b"x" * 100, "UNSUPPORTED_MEDIA_TYPE"),
    (b"\x89PNG\r\n\x1a\n" + b"garbage" * 30, "DOCUMENT_INVALID"),
    (b"", "DOCUMENT_INVALID"),  # pre-Phase 9: an empty file was misreported as an unsupported type (415)
])
def test_invalid_documents_rejected_before_any_engine(ocr_client, data, code):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    r = _upload(ocr_client, anm, case_id, data=data)
    assert r.json()["error"]["code"] == code
    if not data:
        assert r.status_code == 400 and r.json()["error"]["details"]["reason"] == "empty"
    assert ocr_client.app.state.ocr_engines.calls == []


def test_oversize_and_bad_form(ocr_client, monkeypatch):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    monkeypatch.setenv("OCR_MAX_BYTES", "1000")
    get_settings.cache_clear()
    assert _upload(ocr_client, anm, case_id).status_code == 413
    get_settings.cache_clear()
    monkeypatch.delenv("OCR_MAX_BYTES")
    r = ocr_client.post("/api/v1/intake/document", headers=auth(anm), data={"case_id": case_id, "document_type": "x-ray", "idempotency_key": str(uuid.uuid4())},
                        files={"file": ("a.png", b"x", "image/png")})
    assert r.status_code == 400
    r = ocr_client.post("/api/v1/intake/document", headers=auth(anm), data={"case_id": case_id, "document_type": "lab_report", "idempotency_key": str(uuid.uuid4()), "note": "free text"},
                        files={"file": ("a.png", b"x", "image/png")})
    assert r.status_code == 400


def test_quality_rejection_asks_for_retake(ocr_client):
    import io

    from PIL import Image, ImageFilter

    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    buf = io.BytesIO()
    Image.open(FIX / "cbc_low_platelet.png").filter(ImageFilter.GaussianBlur(3)).save(buf, format="PNG")
    r = _upload(ocr_client, anm, case_id, data=buf.getvalue())
    assert r.status_code == 422 and r.json()["error"]["code"] == "DOCUMENT_QUALITY_LOW" and "blurry" in r.json()["error"]["details"]["reasons"]
    assert ocr_client.app.state.ocr_engines.calls == []


# ── authorisation, privacy, triage boundary ───────────────────────────────────────────────────────


def test_authorisation_matrix(ocr_client):
    anm, mo, sup, pat, other = (token_for(ocr_client, w) for w in ("anm", "mo", "supervisor", "patient", "anm_other"))
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    base = f"/api/v1/cases/{case_id}/documents"
    # A patient may upload only to a case its own account created (tests/ocr/test_patient_upload.py); an ANM's case is 404.
    assert _upload(ocr_client, pat, case_id).status_code == 404
    assert ocr_client.get(base, headers=auth(mo)).status_code == 200
    for tok in (sup, other):
        assert ocr_client.get(base, headers=auth(tok)).status_code == 404
        assert ocr_client.get(f"{base}/{doc['document_id']}/pages/0/image", headers=auth(tok)).status_code == 404
    assert ocr_client.get(base).status_code == 401
    # cross-case IDOR: a real document id under a different case
    case2 = _ready_case(ocr_client, anm)
    assert ocr_client.get(f"/api/v1/cases/{case2}/documents/{doc['document_id']}", headers=auth(anm)).status_code == 404
    assert ocr_client.get(f"/api/v1/cases/{case2}/documents/{doc['document_id']}/pages/0/image", headers=auth(anm)).status_code == 404
    f = doc["fields"][0]
    assert _review(ocr_client, anm, case2, f, "unsure").status_code == 404


def test_no_document_text_in_logs_or_audit_and_no_triage_written(ocr_client, caplog):
    caplog.set_level(logging.DEBUG)
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    _review(ocr_client, anm, case_id, doc["fields"][0], "corrected", {"result": {"value": "9.9"}})
    logs = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    audit = json.dumps(audit_rows(ocr_client))
    for secret in CANARY + ("85,000", "11.2", "9.9", "Haemoglobin"):
        assert secret not in logs and secret not in audit, secret
    actions = [r["action"] for r in audit_rows(ocr_client)]
    assert {"ocr_document_started", "ocr_document_processed", "ocr_attestation_recorded", "ocr_review_resolved"} <= set(actions)
    with sqlite3.connect(get_settings().database_path) as c:
        assert c.execute("SELECT count(*) FROM triage_runs").fetchone()[0] == 0


def test_tables_are_append_only(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    _upload(ocr_client, anm, case_id)
    with sqlite3.connect(get_settings().database_path) as c:
        for table in ("ocr_documents", "ocr_pages", "ocr_fields"):
            for stmt in (f"UPDATE {table} SET page_index = page_index" if table != "ocr_documents" else "UPDATE ocr_documents SET status = 'failed'", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    c.execute(stmt)


def test_comparator_cannot_be_dropped_by_a_correction(ocr_client):
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ocr_engines = FakeEngines("two_page_scan")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id, "two_page_scan.pdf").json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    trop = next(f for f in doc["fields"] if f["analyte_key"] == "troponin_i")
    assert trop["value"]["comparator"] == "<"
    r = _review(ocr_client, anm, case_id, trop, "corrected", {"result": {"value": "0.01"}})
    assert r.status_code == 400 and r.json()["error"]["details"]["reason"] == "comparator_required"
    ok = _review(ocr_client, anm, case_id, trop, "corrected", {"result": {"value": "0.01", "comparator": "<"}})
    assert ok.status_code == 200 and ok.json()["reviewed_value"]["comparator"] == "<"


def test_page_write_failure_leaves_no_files_and_fails_explicitly(ocr_client, monkeypatch):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    from app.ocr import service

    import os

    real_fdopen = os.fdopen
    calls = {"n": 0}

    def flaky(fd, *a, **k):
        calls["n"] += 1
        os.close(fd)
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", flaky)
    r = _upload(ocr_client, anm, case_id)
    monkeypatch.setattr(os, "fdopen", real_fdopen)
    assert calls["n"] >= 1
    assert r.status_code == 500 and r.json()["error"]["details"]["reason"] == "storage_failed"
    assert not list(Path(get_settings().ocr_document_dir).rglob("*.png"))
    with sqlite3.connect(get_settings().database_path) as c:
        assert c.execute("SELECT status, failure_code FROM ocr_documents").fetchone() == ("failed", "storage_failed")


def test_surya_bad_response_is_recorded_not_fatal(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)

    class Broken(FakeEngines):
        def surya_page(self, png):
            return {"unexpected": True}

    ocr_client.app.state.ocr_engines = Broken("cbc_low_platelet")
    doc = _upload(ocr_client, anm, case_id).json()
    assert doc["engines"]["surya"] == "failed:bad_response" and all(f["band"] != "accept" for f in doc["fields"])


def test_page_files_are_owner_only(ocr_client):
    import stat

    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    _upload(ocr_client, anm, case_id)
    root = Path(get_settings().ocr_document_dir)
    files = list(root.rglob("*.png"))
    assert files and all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700 and stat.S_IMODE((root / case_id).stat().st_mode) == 0o700


def test_too_many_concurrent_uploads_rejected_before_reading(ocr_client, monkeypatch):
    from app.routes import ocr as route

    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    monkeypatch.setattr(route, "_inflight", route._MAX_INFLIGHT_UPLOADS)
    r = _upload(ocr_client, anm, case_id)
    assert r.status_code == 503 and r.json()["error"]["details"]["reason"] == "too_many_uploads"
    assert ocr_client.app.state.ocr_engines.calls == []
