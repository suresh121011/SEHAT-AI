"""Medical image visual-findings pipeline (architecture §10A; docs/18). Label: `mocked_only`.

Every backend here is the canned `fake` backend or an AsyncMock double injected through `app.state.medgemma_backend`
(like `app.state.ocr_engines`). No API key, no google-genai SDK and no network are needed. Synthetic images only.
The urgency keyword rules under test are NOT clinician-validated.
"""

import io
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings
from app.ocr import medgemma
from app.ocr.image_classifier import classify_upload, resolve
from tests.ocr.test_api import _ready_case, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import audit_rows, auth, grant, new_case, token_for, triage

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "medgemma" / "model_outputs.json").read_text())["cases"]
FX = {c["id"]: c for c in FIXTURES}
API = "/api/v1"


# ── helpers ───────────────────────────────────────────────────────────────────────────────────────


def _png(size=(64, 48), color=(200, 200, 200)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (40, 30), (120, 60, 30))
    exif = Image.Exif()
    exif[0x010F] = "CanaryCameraMaker"  # Make
    exif[0x0112] = 1
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


class StubBackend:
    """AsyncMock-backed double with the ImageBackend interface."""

    def __init__(self, output=None, *, name="fake", cloud=False, side_effect=None):
        self.name, self.model, self.cloud = name, "stub-model-1", cloud
        self.describe = AsyncMock(return_value=output, side_effect=side_effect)


def _enable(monkeypatch, backend="fake", **extra):
    monkeypatch.setenv("MEDGEMMA_ENABLED", "1")
    monkeypatch.setenv("MEDGEMMA_BACKEND", backend)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def _enable_cloud(monkeypatch):
    _enable(monkeypatch, "google_ai", AI_CLOUD_ENABLED="1", AI_CLOUD_SYNTHETIC_DATA_ONLY="1", GOOGLE_AI_API_KEY="test-key-not-real")


def _img(client, token, case_id, doc_type, filename="image.png", data=None, attest=None, key=None, ctype="image/png"):
    form = {"case_id": case_id, "document_type": doc_type, "idempotency_key": key or str(uuid.uuid4())}
    if attest is not None:
        form["synthetic_attestation"] = attest
    return client.post(f"{API}/intake/document", headers=auth(token), data=form, files={"file": (filename, data if data is not None else _png(), ctype)})


def _db():
    c = sqlite3.connect(get_settings().database_path)
    c.row_factory = sqlite3.Row
    return c


def _actions(client, action):
    return [r for r in audit_rows(client) if r["action"] == action]


# ── classifier (pure) ─────────────────────────────────────────────────────────────────────────────


def test_classify_upload_ecg():
    assert classify_upload("ecg_strip.jpg", "image/jpeg", b"\xff\xd8\xff\xe0") == "ecg_strip"


def test_classify_upload_lab_report():
    assert classify_upload("blood_test_results.pdf", "application/pdf", b"%PDF-1.7\n") == "lab_report"
    assert classify_upload("blood_test_results.pdf", "application/pdf", None) == "lab_report"


def test_classify_upload_matches_whole_tokens_only():
    assert classify_upload("doctor_note.jpg", "image/jpeg", b"\xff\xd8\xff") is None  # "ct" inside "doctor" is not a CT
    assert classify_upload("CT-scan_head.png", "image/png", b"\x89PNG\r\n\x1a\n") == "ct_report_image"
    assert classify_upload("patient x-ray chest.jpeg", None, None) == "chest_xray"
    assert classify_upload("IMG_2041.jpg", "image/jpeg", b"\xff\xd8\xff") is None
    assert resolve("ecg_strip", None) == ("ecg_strip", False)
    assert resolve("ct_report_image", "chest_xray") == ("ct_report_image", True)  # declared type always wins


# ── guard + urgency on canned fixtures (pure) ────────────────────────────────────────────────────


@pytest.mark.parametrize("case", FIXTURES, ids=[c["id"] for c in FIXTURES])
def test_fixture_outputs_guard_and_urgency(case):
    raw = medgemma.parse_output(case["output"], case["image_type"])
    f = medgemma.filter_findings(raw)
    signals = medgemma.check_image_urgency(f.fields, case["image_type"])
    exp = case["expect"]
    assert f.withheld_fields == exp["withheld_fields"]
    assert f.description_withheld is exp["description_withheld"]
    assert sorted(set(f.reasons)) == exp["reasons"]
    assert [[s["signal"], s["action"], s["negated"]] for s in signals] == exp["signals"]
    for key in f.withheld_fields:  # withheld text never survives into what is shown
        assert key not in f.fields


def test_negated_pneumothorax_is_review_note_not_red():
    sig = medgemma.check_image_urgency({"abnormalities": "No evidence of pneumothorax"}, "chest_xray")
    assert sig == [{"signal": "CRITICAL_IMAGING_FINDING", "action": "REVIEW_NOTE", "note": sig[0]["note"], "source": "MedGemma image analysis",
                    "rule_set": "chest_xray", "negated": True}]


def test_hedged_pneumothorax_still_raises_red():
    for text in ("cannot rule out pneumothorax", "possible small pneumothorax", "pneumothorax cannot be excluded", "suspicious for pneumothorax"):
        sig = medgemma.check_image_urgency({"abnormalities": text}, "chest_xray")
        assert [(s["action"], s["negated"]) for s in sig] == [("RED_FLAG", False)], text


def test_confirmed_case_of_is_blocked_by_the_shared_guard():
    from app.ai import guard

    assert guard.check("This is a confirmed case of tuberculosis") == "diagnostic_language"


# ── API ───────────────────────────────────────────────────────────────────────────────────────────


def test_medgemma_disabled_returns_not_available(ocr_client, monkeypatch):
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")  # not delenv: a developer .env would refill it
    get_settings.cache_clear()
    stub = StubBackend(FX["st_elevation"]["output"])
    ocr_client.app.state.medgemma_backend = stub
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    r = _img(ocr_client, anm, case_id, "chest_xray", filename="chest_xray.png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pipeline"] == "medgemma" and body["status"] == "not_available" and body["not_available_reason"] == "disabled"
    assert "MEDGEMMA_ENABLED" in body["note"] and body["disclaimer"] == medgemma.DISCLAIMER
    assert body["classifier_hint"] == "chest_xray" and body["classifier_mismatch"] is False
    assert body["raw_description"] is None and body["structured_fields"] == {} and body["urgency_signals"] == []
    assert body["source_image_url"] == f"/api/backend/cases/{case_id}/medical-images/{body['document_id']}/image"
    stub.describe.assert_not_awaited()
    with _db() as c:
        row = c.execute("SELECT status, not_available_reason, file_ref FROM medical_images WHERE document_id = ?", (body["document_id"],)).fetchone()
    assert tuple(row)[:2] == ("not_available", "disabled")
    assert (Path(get_settings().ocr_document_dir) / row["file_ref"]).is_file()
    assert _actions(ocr_client, "medgemma_image_not_available")
    img = ocr_client.get(f"{API}/cases/{case_id}/medical-images/{body['document_id']}/image", headers=auth(anm))
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


def test_non_diagnostic_guard_blocks_diagnosis_language(ocr_client, monkeypatch):
    _enable(monkeypatch)
    ocr_client.app.state.medgemma_backend = StubBackend(FX["bare_label_pneumonia"]["output"])
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "chest_xray").json()
    assert body["status"] == "described"
    assert body["withheld"] == {"fields": ["abnormalities"], "description": True, "reasons": ["diagnostic_language"]}
    assert body["raw_description"] is None and "abnormalities" not in body["structured_fields"]
    assert "pneumonia" not in json.dumps(body).lower()  # the withheld text is never returned
    blocked = _actions(ocr_client, "medgemma_diagnosis_blocked")
    assert blocked and "pneumonia" not in blocked[0]["details_json"].lower()
    assert json.loads(blocked[0]["details_json"])["withheld_field_count"] == 1


def test_urgency_signal_st_elevation(ocr_client, monkeypatch):
    _enable(monkeypatch)
    ocr_client.app.state.medgemma_backend = StubBackend(FX["st_elevation"]["output"])
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "ecg_strip", filename="ecg_strip.jpg").json()
    assert [(s["signal"], s["action"], s["rule_set"], s["source"]) for s in body["urgency_signals"]] == [
        ("ST_ELEVATION", "RED_FLAG", "ecg_strip", "MedGemma image analysis")]
    assert body["requires_acknowledgement"] is True and body["acknowledged"] is False
    assert body["raw_description"].endswith(medgemma.MANDATORY_SUFFIX)
    assert body["keyword_rules_validated"] is False and body["rule_sets_run"] == ["ecg_strip"]
    assert body["structured_fields"]["st_segment"] == "ST elevation in leads V1-V4"


def test_urgency_signal_chest_xray_pneumothorax(ocr_client, monkeypatch):
    _enable(monkeypatch)
    ocr_client.app.state.medgemma_backend = StubBackend(FX["pneumothorax"]["output"])
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "chest_xray").json()
    assert [(s["signal"], s["action"], s["negated"]) for s in body["urgency_signals"]] == [("CRITICAL_IMAGING_FINDING", "RED_FLAG", False)]
    assert body["requires_acknowledgement"] is True


def test_withheld_field_still_raises_its_urgency_signal(ocr_client, monkeypatch):
    """The guard withholds diagnostic wording, but it must never hide a raise-only signal; the note carries only the keyword."""
    _enable(monkeypatch)
    ocr_client.app.state.medgemma_backend = StubBackend({
        "description": "Absent lung markings on the left. Findings consistent with tension pneumothorax.",
        "fields": {"abnormalities": "consistent with tension pneumothorax"}, "confidence": 0.9})
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "chest_xray").json()
    assert "abnormalities" in body["withheld"]["fields"] and "abnormalities" not in body["structured_fields"]
    assert [(s["signal"], s["action"]) for s in body["urgency_signals"]] == [("CRITICAL_IMAGING_FINDING", "RED_FLAG")]
    assert "consistent with" not in json.dumps(body["urgency_signals"]).lower()
    assert body["requires_acknowledgement"] is True


def test_low_confidence_is_label_only(ocr_client, monkeypatch):
    """Adapted from the original `test_low_confidence_returns_raw_only`: the user chose (docs/18 §6) that self-reported,
    uncalibrated confidence is a LABEL only and never hides findings, description or urgency signals."""
    _enable(monkeypatch)
    out = {**FX["st_elevation"]["output"], "confidence": 0.3}
    ocr_client.app.state.medgemma_backend = StubBackend(out)
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "ecg_strip").json()
    assert body["status"] == "described" and body["confidence"] == 0.3 and body["confidence_band"] == "low"
    assert body["structured_fields"]["st_segment"] and body["raw_description"]
    assert [s["action"] for s in body["urgency_signals"]] == ["RED_FLAG"]
    assert "low confidence" in body["note"]
    assert medgemma.confidence_band(0.81) == "high" and medgemma.confidence_band(0.5) == "moderate" and medgemma.confidence_band(None) is None


def test_audit_log_records_medgemma_call(ocr_client, monkeypatch):
    _enable(monkeypatch)
    ocr_client.app.state.medgemma_backend = StubBackend(FX["st_elevation"]["output"])
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "ecg_strip").json()
    rows = _actions(ocr_client, "medgemma_image_analyzed")
    assert len(rows) == 1 and rows[0]["outcome"] == "success" and rows[0]["case_id"] == case_id
    d = json.loads(rows[0]["details_json"])
    assert d["document_id"] == body["document_id"] and d["backend"] == "fake" and d["model"] == "stub-model-1"
    assert d["status"] == "described" and d["signal_codes"] == ["ST_ELEVATION"] and d["red_count"] == 1 and d["rule_sets"] == ["ecg_strip"]
    raw = rows[0]["details_json"].lower()
    assert "v1-v4" not in raw and "regular rhythm" not in raw  # codes and counts only, never model text


def test_classifier_mismatch_runs_both_rule_sets_and_needs_acknowledgement(ocr_client, monkeypatch):
    _enable(monkeypatch)
    out = {"description": "Axial slice through the lower chest.", "fields": {"region": "lower chest", "abnormalities": "small pneumothorax at the left base"},
           "confidence": 0.7}
    ocr_client.app.state.medgemma_backend = StubBackend(out)
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "ct_report_image", filename="chest_xray_film.png").json()
    assert body["declared_type"] == body["image_class"] == "ct_report_image"
    assert body["classifier_hint"] == "chest_xray" and body["classifier_mismatch"] is True
    assert body["rule_sets_run"] == ["ct_report_image", "chest_xray"]
    assert [(s["rule_set"], s["action"]) for s in body["urgency_signals"]] == [("chest_xray", "RED_FLAG")]
    assert body["requires_acknowledgement"] is True
    # a mismatch alone (no signal) still needs acknowledgement
    ocr_client.app.state.medgemma_backend = StubBackend({"description": "Axial slice.", "fields": {"region": "head"}, "confidence": 0.7})
    body2 = _img(ocr_client, anm, case_id, "ct_report_image", filename="wound_left_leg.png").json()
    assert body2["classifier_mismatch"] is True and body2["urgency_signals"] == [] and body2["requires_acknowledgement"] is True


def test_sign_off_blocked_until_image_findings_acknowledged(ocr_client, monkeypatch):
    _enable(monkeypatch)
    ocr_client.app.state.medgemma_backend = StubBackend(FX["st_elevation"]["output"])
    anm, mo = token_for(ocr_client, "anm"), token_for(ocr_client, "mo")
    case_id = _ready_case(ocr_client, anm)
    r = triage(ocr_client, anm, case_id)
    assert r.status_code in (200, 201), r.text
    img = _img(ocr_client, anm, case_id, "ecg_strip").json()
    review = ocr_client.get(f"{API}/triage/{case_id}", headers=auth(mo)).json()
    run_id, urgency_before = review["latest"]["triage_run_id"], review["latest"]["rules_urgency"]
    assert [(f["signal"], f["action"]) for f in review["image_findings"]["flags"]] == [("ST_ELEVATION", "RED_FLAG")]
    assert review["image_findings"]["unacknowledged"] == 1
    r = ocr_client.patch(f"{API}/triage/{case_id}/sign-off", json={"triage_run_id": run_id, "confirm": True}, headers=auth(mo))
    assert r.status_code == 409 and r.json()["error"]["code"] == "IMAGE_FINDINGS_NOT_REVIEWED"
    assert r.json()["error"]["details"]["document_ids"] == [img["document_id"]]
    ack = ocr_client.post(f"{API}/cases/{case_id}/medical-images/{img['document_id']}/acknowledge", json={}, headers=auth(mo))
    assert ack.status_code == 200 and ack.json()["acknowledged"] is True
    again = ocr_client.post(f"{API}/cases/{case_id}/medical-images/{img['document_id']}/acknowledge", json={}, headers=auth(mo))
    assert again.status_code == 200 and len(_actions(ocr_client, "medgemma_findings_acknowledged")) == 1  # idempotent
    with _db() as c:
        assert c.execute("SELECT count(*) FROM medical_image_acknowledgements").fetchone()[0] == 1
    r = ocr_client.patch(f"{API}/triage/{case_id}/sign-off", json={"triage_run_id": run_id, "confirm": True}, headers=auth(mo))
    assert r.status_code == 200, r.text
    after = ocr_client.get(f"{API}/triage/{case_id}", headers=auth(mo)).json()
    assert after["latest"]["rules_urgency"] == urgency_before  # image flags never rewrite the rules-engine urgency
    listed = ocr_client.get(f"{API}/cases/{case_id}/medical-images", headers=auth(mo)).json()
    assert listed["case_id"] == case_id and listed["medical_images"][0]["acknowledged"] is True


def test_cloud_backend_needs_attestation_and_ai_assist_consent(ocr_client, monkeypatch):
    _enable_cloud(monkeypatch)
    stub = StubBackend(FX["st_elevation"]["output"], name="google_ai", cloud=True)
    ocr_client.app.state.medgemma_backend = stub
    anm = token_for(ocr_client, "anm")
    case_ai = new_case(ocr_client, anm)
    assert grant(ocr_client, anm, case_ai, ai=True).status_code == 200
    body = _img(ocr_client, anm, case_ai, "ecg_strip").json()  # no attestation
    assert body["status"] == "not_available" and body["not_available_reason"] == "synthetic_attestation_missing" and "synthetic" in body["note"]
    body = _img(ocr_client, anm, case_ai, "ecg_strip", attest="false").json()
    assert body["not_available_reason"] == "synthetic_attestation_missing"
    stub.describe.assert_not_awaited()
    case_no_ai = _ready_case(ocr_client, anm)  # triage consent only
    body = _img(ocr_client, anm, case_no_ai, "ecg_strip", attest="true").json()
    assert body["status"] == "not_available" and body["not_available_reason"] == "consent_ai_assist_missing"
    stub.describe.assert_not_awaited()
    body = _img(ocr_client, anm, case_ai, "ecg_strip", attest="true").json()
    assert body["status"] == "described" and body["backend"] == "google_ai"
    stub.describe.assert_awaited_once()
    assert _img(ocr_client, anm, case_ai, "ecg_strip", attest="maybe").status_code == 400


def test_config_refuses_cloud_backends_without_gates(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("OCR_ENABLED", "1")
    monkeypatch.setenv("OCR_RETENTION_DAYS", "none")
    monkeypatch.setenv("MEDGEMMA_ENABLED", "1")
    monkeypatch.setenv("AI_CLOUD_ENABLED", "0")
    monkeypatch.setenv("AI_CLOUD_SYNTHETIC_DATA_ONLY", "0")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "")
    for name in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT_NAME", "AZURE_OPENAI_API_VERSION"):
        monkeypatch.setenv(name, "")
    try:
        monkeypatch.setenv("MEDGEMMA_BACKEND", "google_ai")
        with pytest.raises(RuntimeError, match="AI_CLOUD_ENABLED"):
            get_settings()
        monkeypatch.setenv("AI_CLOUD_ENABLED", "1")
        with pytest.raises(RuntimeError, match="AI_CLOUD_SYNTHETIC_DATA_ONLY"):
            get_settings()
        monkeypatch.setenv("AI_CLOUD_SYNTHETIC_DATA_ONLY", "1")
        with pytest.raises(RuntimeError, match="GOOGLE_AI_API_KEY"):
            get_settings()
        monkeypatch.setenv("MEDGEMMA_BACKEND", "azure")
        with pytest.raises(RuntimeError, match="AZURE_OPENAI_API_KEY"):
            get_settings()
        monkeypatch.setenv("MEDGEMMA_BACKEND", "bogus")
        with pytest.raises(RuntimeError, match="MEDGEMMA_BACKEND"):
            get_settings()
        monkeypatch.setenv("MEDGEMMA_BACKEND", "google_ai")
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key-not-real")
        s = get_settings()
        assert s.medgemma_enabled and s.medgemma_model == "gemini-3.8-flash" and "test-key" not in repr(s)
        get_settings.cache_clear()
        monkeypatch.setenv("OCR_ENABLED", "0")
        with pytest.raises(RuntimeError, match="OCR_ENABLED"):
            get_settings()
        get_settings.cache_clear()
        monkeypatch.setenv("OCR_ENABLED", "1")
        monkeypatch.setenv("MEDGEMMA_BACKEND", "fake")  # fake needs no cloud gate
        monkeypatch.setenv("AI_CLOUD_ENABLED", "0")
        monkeypatch.setenv("AI_CLOUD_SYNTHETIC_DATA_ONLY", "0")
        assert get_settings().medgemma_backend == "fake"
    finally:
        get_settings.cache_clear()


def test_backend_timeout_returns_failed_and_keeps_image(ocr_client, monkeypatch):
    _enable(monkeypatch)
    ocr_client.app.state.medgemma_backend = StubBackend(side_effect=TimeoutError())
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    r = _img(ocr_client, anm, case_id, "chest_xray")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed" and body["raw_description"] is None and body["urgency_signals"] == []
    assert "took too long" in body["note"] and body["disclaimer"] == medgemma.DISCLAIMER
    img = ocr_client.get(f"{API}/cases/{case_id}/medical-images/{body['document_id']}/image", headers=auth(anm))
    assert img.status_code == 200
    rows = _actions(ocr_client, "medgemma_image_analyzed")
    assert rows[-1]["outcome"] == "failure" and json.loads(rows[-1]["details_json"])["reason_code"] == "backend_timeout"
    ocr_client.app.state.medgemma_backend = StubBackend("this is not json")
    assert _img(ocr_client, anm, case_id, "chest_xray").json()["status"] == "failed"


def test_text_type_upload_still_goes_to_ocr_path(ocr_client, monkeypatch):
    _enable(monkeypatch)
    stub = StubBackend(FX["st_elevation"]["output"])
    ocr_client.app.state.medgemma_backend = stub
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    r = _upload(ocr_client, anm, case_id)  # lab_report via the existing helper
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed" and "pipeline" not in r.json()
    stub.describe.assert_not_awaited()
    with _db() as c:
        assert c.execute("SELECT count(*) FROM medical_images").fetchone()[0] == 0
    assert ocr_client.app.state.ocr_engines.calls  # the OCR engines ran


def test_pdf_and_bad_images_rejected_for_image_types(ocr_client, monkeypatch):
    _enable(monkeypatch)
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    r = _img(ocr_client, anm, case_id, "ecg_strip", filename="ecg.pdf", data=b"%PDF-1.4\n" + b"x" * 50, ctype="application/pdf")
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    r = _img(ocr_client, anm, case_id, "ecg_strip", data=b"\x89PNG\r\n\x1a\n" + b"garbage" * 20)
    assert r.status_code == 400 and r.json()["error"]["code"] == "DOCUMENT_INVALID"


def test_stored_image_is_metadata_stripped_and_served_safely(ocr_client, monkeypatch):
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")  # not delenv: a developer .env would refill it
    get_settings.cache_clear()
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    data = _jpeg_with_exif()
    assert b"CanaryCameraMaker" in data
    body = _img(ocr_client, anm, case_id, "wound_photo", filename="wound.jpg", data=data, ctype="image/jpeg").json()
    r = ocr_client.get(f"{API}/cases/{case_id}/medical-images/{body['document_id']}/image", headers=auth(anm))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff" and r.headers["content-disposition"] == "inline"
    assert b"CanaryCameraMaker" not in r.content and b"Exif" not in r.content
    # idempotent replay returns the same row
    key = str(uuid.uuid4())
    a = _img(ocr_client, anm, case_id, "chest_xray", key=key).json()
    b = _img(ocr_client, anm, case_id, "chest_xray", key=key).json()
    assert a["document_id"] == b["document_id"]
    assert _img(ocr_client, anm, case_id, "ecg_strip", key=key).status_code == 409


def test_capabilities_report_medgemma(ocr_client, monkeypatch):
    anm = token_for(ocr_client, "anm")
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")  # not delenv: a developer .env would refill it
    get_settings.cache_clear()
    caps = ocr_client.get(f"{API}/intake/document/capabilities", headers=auth(anm)).json()
    assert "xray_ecg" not in caps["document_types"]
    assert {k: caps["document_types"][k] for k in ("chest_xray", "ecg_strip", "ct_report_image", "wound_photo", "skin_lesion")} == dict.fromkeys(
        ("chest_xray", "ecg_strip", "ct_report_image", "wound_photo", "skin_lesion"), False)
    assert caps["medgemma_enabled"] is False and caps["medgemma_ready"] is False and caps["medgemma_cloud"] is False
    assert len(caps["supported_image_types"]) == 8 and caps["verification"]["medgemma"] == "mocked_only"
    assert {"ocr_enabled", "processing", "engines", "max_bytes", "retention_days", "max_pages"} <= set(caps)
    _enable(monkeypatch)
    caps = ocr_client.get(f"{API}/intake/document/capabilities", headers=auth(anm)).json()
    assert caps["medgemma_enabled"] and caps["medgemma_ready"] and caps["medgemma_backend"] == "fake" and caps["medgemma_model"] == "fake-canned-v1"
    assert caps["document_types"]["ecg_strip"] is True and caps["medgemma_cloud"] is False


def test_fake_backend_end_to_end_without_injection(ocr_client, monkeypatch):
    _enable(monkeypatch)
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "ecg_strip").json()
    assert body["status"] == "described" and body["backend"] == "fake" and body["model"] == "fake-canned-v1"
    assert [s["signal"] for s in body["urgency_signals"]] == ["ST_ELEVATION"]
    assert body["confidence_band"] == "high" and body["prompt_version"] == medgemma.PROMPT_VERSION and body["guard_version"] == medgemma.GUARD_VERSION


def test_delete_and_retention_cover_images(ocr_client, monkeypatch):
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")  # not delenv: a developer .env would refill it
    get_settings.cache_clear()
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    a = _img(ocr_client, anm, case_id, "skin_lesion").json()
    b = _img(ocr_client, anm, case_id, "skin_lesion").json()
    root = Path(get_settings().ocr_document_dir)
    r = ocr_client.delete(f"{API}/cases/{case_id}/medical-images/{a['document_id']}", headers=auth(anm))
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    assert not (root / case_id / f"{a['document_id']}.img").exists()
    assert ocr_client.get(f"{API}/cases/{case_id}/medical-images/{a['document_id']}/image", headers=auth(anm)).status_code == 404
    assert ocr_client.delete(f"{API}/cases/{case_id}/medical-images/{a['document_id']}", headers=auth(anm)).json()["already_deleted"] is True
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="microseconds")
    with _db() as c:
        c.execute("DROP TRIGGER medical_images_final")  # test-only: age the synthetic image
        c.execute("UPDATE medical_images SET created_at = ? WHERE document_id = ?", (old, b["document_id"]))
    monkeypatch.setenv("OCR_RETENTION_DAYS", "30")
    get_settings.cache_clear()
    listed = ocr_client.get(f"{API}/cases/{case_id}/medical-images", headers=auth(anm)).json()
    assert listed["medical_images"] == []
    assert not (root / case_id / f"{b['document_id']}.img").exists()
    deleted = _actions(ocr_client, "medgemma_image_deleted")
    assert {json.loads(d["details_json"])["reason"] for d in deleted} == {"reviewer_request", "retention_expired"}


# ── real adapters, mocked (no SDK install, no key, no network) ───────────────────────────────────


def _settings_ns(**kw):
    from types import SimpleNamespace

    base = dict(medgemma_model="gemini-3.8-flash", medgemma_timeout_s=5.0, google_ai_api_key="k", azure_openai_api_key="k",
                azure_openai_endpoint="https://x.openai.azure.com/", azure_openai_deployment_name="vision-dep", azure_openai_api_version="2024-10-21")
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.asyncio
async def test_google_ai_backend_contract_with_a_fake_sdk(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    calls = {}

    class Part:
        @staticmethod
        def from_bytes(data, mime_type):
            calls["part"] = (len(data), mime_type)
            return ("part", mime_type)

    class GenerateContentConfig:
        def __init__(self, **kw):
            calls["config"] = kw

    types_mod = ModuleType("google.genai.types")
    types_mod.Part, types_mod.GenerateContentConfig = Part, GenerateContentConfig
    genai_mod = ModuleType("google.genai")
    genai_mod.types = types_mod
    google_mod = sys.modules.get("google") or ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setattr(google_mod, "genai", genai_mod, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)
    gen = AsyncMock(return_value=SimpleNamespace(text=json.dumps(FX["st_elevation"]["output"])))
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=gen)))
    backend = medgemma.GoogleAIBackend(_settings_ns(), client=client)
    out = await backend.describe(b"\x89PNG....", "image/png", "prompt text", image_type="ecg_strip")
    assert medgemma.parse_output(out, "ecg_strip").fields["st_segment"]
    kw = gen.await_args.kwargs
    assert kw["model"] == "gemini-3.8-flash" and kw["contents"][1] == "prompt text"
    assert calls["config"] == {"temperature": 0.1, "max_output_tokens": 500, "response_mime_type": "application/json"}
    assert calls["part"][1] == "image/png" and backend.cloud is True


@pytest.mark.asyncio
async def test_azure_vision_backend_contract_with_a_fake_service():
    from types import SimpleNamespace

    from semantic_kernel.contents import ImageContent

    svc = SimpleNamespace(get_chat_message_content=AsyncMock(return_value=SimpleNamespace(content=json.dumps(FX["pneumothorax"]["output"]))))
    backend = medgemma.AzureVisionBackend(_settings_ns(), service=svc)
    out = await backend.describe(_png(), "image/png", "prompt text", image_type="chest_xray")
    assert "pneumothorax" in medgemma.parse_output(out, "chest_xray").fields["abnormalities"].lower()
    history, settings = svc.get_chat_message_content.await_args.args
    items = history.messages[-1].items
    assert any(isinstance(i, ImageContent) for i in items) and settings.temperature == 0.1 and settings.max_tokens == 500
    assert backend.model == "vision-dep"


def test_build_image_backend_is_explicit():
    assert medgemma.build_image_backend(_settings_ns(medgemma_enabled=False, medgemma_backend="fake")) is None
    assert isinstance(medgemma.build_image_backend(_settings_ns(medgemma_enabled=True, medgemma_backend="fake")), medgemma.FakeImageBackend)
    assert isinstance(medgemma.build_image_backend(_settings_ns(medgemma_enabled=True, medgemma_backend="google_ai")), medgemma.GoogleAIBackend)
    assert isinstance(medgemma.build_image_backend(_settings_ns(medgemma_enabled=True, medgemma_backend="azure")), medgemma.AzureVisionBackend)


def test_startup_sweep_removes_orphan_image_files_and_keeps_known_ones(ocr_client, monkeypatch):
    import asyncio

    from app.database import _connect
    from app.ocr import service

    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")  # not delenv: a developer .env would refill it
    get_settings.cache_clear()
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    kept = _img(ocr_client, anm, case_id, "chest_xray").json()
    root = Path(get_settings().ocr_document_dir)
    orphan = root / case_id / f"{uuid.uuid4()}.img"
    orphan.write_bytes(b"leftover")

    async def run():
        conn = await _connect(get_settings().database_path)
        try:
            await service.startup_sweep(conn, get_settings())
        finally:
            await conn.close()

    asyncio.run(run())
    assert not orphan.exists()
    assert (root / case_id / f"{kept['document_id']}.img").exists()


# ── Provenance and local vision (docs/18 §4a, §9) ────────────────────────────────────────────────


def test_fake_output_is_labelled_synthetic_not_a_model(ocr_client, monkeypatch):
    _enable(monkeypatch)
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "chest_xray").json()
    assert body["provenance"] == {"provider": "fake", "model": "fake-canned-v1", "mode": "fake", "synthetic": True, "medical_model": False}
    listed = ocr_client.get(f"{API}/cases/{case_id}/medical-images", headers=auth(anm)).json()["medical_images"][0]
    assert listed["provenance"]["synthetic"] is True
    caps = ocr_client.get(f"{API}/intake/document/capabilities", headers=auth(anm)).json()
    assert caps["medgemma_mode"] == "fake" and caps["medgemma_synthetic"] is True


def test_provenance_modes():
    assert medgemma.provenance("google_ai", "gemini-x")["mode"] == "cloud" and medgemma.provenance("google_ai", "gemini-x")["synthetic"] is False
    assert medgemma.provenance("azure", "dep")["mode"] == "cloud"
    assert medgemma.provenance(None, None)["mode"] == "none"


def test_disabled_image_has_no_provider(ocr_client, monkeypatch):
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")
    get_settings.cache_clear()
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    body = _img(ocr_client, anm, case_id, "chest_xray").json()
    assert body["status"] == "not_available" and body["provenance"]["mode"] == "none" and body["provenance"]["synthetic"] is False


# ── Local MedGemma (docs/18 §4a). No model runs here: the real backend class talks to a fake worker. ─────────────

@pytest.fixture
def no_local_build(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "MEDGEMMA_LOCAL_DIR", tmp_path / "absent")
    return tmp_path


@pytest.fixture
def local_build(tmp_path, monkeypatch):
    import app.config as config

    d = tmp_path / "mg"
    d.mkdir()
    (d / "SEHAT_ENGINE_MANIFEST.json").write_text("{}")
    py = tmp_path / "python"
    py.write_text("")
    monkeypatch.setattr(config, "MEDGEMMA_LOCAL_DIR", d)
    monkeypatch.setenv("OCR_WORKER_PYTHON", str(py))
    return d


def test_local_backend_refuses_to_start_when_not_installed_never_faked(monkeypatch, no_local_build):
    _enable(monkeypatch, "local", OCR_ENABLED="1", OCR_RETENTION_DAYS="none")
    with pytest.raises(RuntimeError, match="local MedGemma build is not installed.*No fallback"):
        get_settings()
    get_settings.cache_clear()


def test_local_backend_starts_when_installed_with_a_longer_default_timeout(monkeypatch, local_build):
    _enable(monkeypatch, "local", OCR_ENABLED="1", OCR_RETENTION_DAYS="none")
    s = get_settings()
    assert s.medgemma_backend == "local" and s.medgemma_timeout_s == 120.0
    assert isinstance(medgemma.build_image_backend(s), medgemma.LocalMedGemmaBackend) and medgemma.backend_ready(s)
    get_settings.cache_clear()


def test_capabilities_report_local_vision_unavailable(ocr_client, no_local_build):
    caps = ocr_client.get(f"{API}/intake/document/capabilities", headers=auth(token_for(ocr_client, "anm"))).json()
    assert caps["local_vision"]["available"] is False and caps["local_vision"]["reason"] == "local_model_not_installed"
    assert "ecg_strip" in caps["local_vision"]["unsupported_image_types"] and caps["local_vision"]["validated_medical_device"] is False


def test_capabilities_report_local_vision_installed(ocr_client, local_build):
    lv = ocr_client.get(f"{API}/intake/document/capabilities", headers=auth(token_for(ocr_client, "anm"))).json()["local_vision"]
    assert lv["available"] is True and lv["model"] == medgemma.LOCAL_MODEL_ID and lv["unsupported_image_types"] == ["ecg_strip"]


class FakeWorker:
    def __init__(self, text='{"description": "Both lung fields are expanded.", "fields": {}, "confidence": 0.5, "image_quality": "adequate"}'):
        self.text, self.calls = text, []

    def call(self, path, body, timeout_s):
        self.calls.append((path, json.loads(body), timeout_s))
        return {"engine": "medgemma-1.5-4b-it-mlx-8bit", "text": self.text}


def _local(worker):
    from types import SimpleNamespace

    return medgemma.LocalMedGemmaBackend(SimpleNamespace(medgemma_timeout_s=120.0), worker=worker)


def test_local_backend_sends_image_and_fixed_prompt_only():
    import asyncio
    import base64

    w = FakeWorker()
    out = asyncio.run(_local(w).describe(b"\x89PNGdata", "image/png", "PROMPT", image_type="chest_xray"))
    assert out == w.text
    path, body, timeout = w.calls[0]
    assert path == "/medgemma/describe" and base64.b64decode(body["image_b64"]) == b"\x89PNGdata" and body["prompt"] == "PROMPT"
    assert set(body) == {"image_b64", "prompt", "temperature"} and timeout == 120.0


def test_local_backend_never_sends_ecg_and_rejects_empty_replies():
    import asyncio

    w = FakeWorker()
    with pytest.raises(medgemma.UnsupportedImageType):
        asyncio.run(_local(w).describe(b"x", "image/png", "P", image_type="ecg_strip"))
    assert w.calls == []
    with pytest.raises(medgemma.BadResponse):
        asyncio.run(_local(FakeWorker(text="  ")).describe(b"x", "image/png", "P", image_type="chest_xray"))


def test_local_provenance_is_a_medical_model_but_not_a_validated_device():
    p = medgemma.provenance("local", medgemma.LOCAL_MODEL_ID)
    assert p["mode"] == "local" and p["synthetic"] is False and p["medical_model"] is True and p["validated_medical_device"] is False


def test_api_local_ecg_is_unsupported_and_never_sent(ocr_client, monkeypatch):
    _enable(monkeypatch)
    w = FakeWorker()
    ocr_client.app.state.medgemma_backend = _local(w)
    anm = token_for(ocr_client, "anm")
    case_id = new_case(ocr_client, anm)
    assert grant(ocr_client, anm, case_id, ai=True).status_code == 200
    body = _img(ocr_client, anm, case_id, "ecg_strip", filename="ecg_strip.png").json()
    assert body["status"] == "unsupported_type" and "ECG is outside its model card" in body["note"] and w.calls == []
    assert body["urgency_signals"] == [] and not body.get("raw_description")
    assert json.loads(_actions(ocr_client, "medgemma_image_not_available")[-1]["details_json"])["reason"] == "unsupported_image_type"


def test_api_local_needs_ai_assist_consent_but_no_cloud_attestation(ocr_client, monkeypatch):
    _enable(monkeypatch)
    w = FakeWorker()
    ocr_client.app.state.medgemma_backend = _local(w)
    anm = token_for(ocr_client, "anm")
    no_ai = _ready_case(ocr_client, anm)  # triage consent only
    body = _img(ocr_client, anm, no_ai, "chest_xray").json()
    assert body["status"] == "not_available" and body["not_available_reason"] == "consent_ai_assist_missing" and w.calls == []

    case_id = new_case(ocr_client, anm)
    assert grant(ocr_client, anm, case_id, ai=True).status_code == 200
    body = _img(ocr_client, anm, case_id, "chest_xray").json()  # no synthetic attestation: the image stays on this machine
    assert body["status"] == "described" and len(w.calls) == 1
    assert body["provenance"]["mode"] == "local" and body["provenance"]["synthetic"] is False and body["provenance"]["medical_model"] is True


def synthetic_chest_like_png() -> bytes:
    """Procedurally drawn, obviously synthetic chest-radiograph-like picture (no patient, no real image): two dark lung
    fields, a spine and rib arcs on a light background. Exercises the runtime only; it says nothing about accuracy."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("L", (512, 512), 200)
    d = ImageDraw.Draw(img)
    d.ellipse((90, 90, 240, 430), fill=60)
    d.ellipse((272, 90, 422, 430), fill=60)
    d.rectangle((246, 40, 266, 480), fill=225)
    for y in range(110, 420, 32):
        d.arc((80, y - 40, 250, y + 40), 200, 340, fill=170, width=6)
        d.arc((262, y - 40, 432, y + 40), 200, 340, fill=170, width=6)
    buf = io.BytesIO()
    img.filter(ImageFilter.GaussianBlur(3)).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.live
@pytest.mark.skipif(__import__("os").environ.get("RUN_LIVE_MEDGEMMA_TESTS") != "1",
                    reason="set RUN_LIVE_MEDGEMMA_TESTS=1 after scripts/download_medgemma_local.py (runs the real local model)")
def test_live_local_medgemma_describes_a_synthetic_image():
    """Runtime smoke test of the real local model through the real worker. Prints the output for the human running it;
    asserts only that the reply parses and passes through the non-diagnostic guard. NOT an evaluation."""
    import asyncio
    import time

    from app.ocr.worker_client import shutdown_all

    from dataclasses import replace

    get_settings.cache_clear()
    s = replace(get_settings(), medgemma_enabled=True, medgemma_backend="local", medgemma_timeout_s=120.0)  # independent of .env
    backend = medgemma.LocalMedGemmaBackend(s)
    try:
        t = time.perf_counter()
        reply = asyncio.run(backend.describe(synthetic_chest_like_png(), "image/png", medgemma.build_prompt("chest_xray"), image_type="chest_xray"))
        first = time.perf_counter() - t
        t = time.perf_counter()
        asyncio.run(backend.describe(synthetic_chest_like_png(), "image/png", medgemma.build_prompt("chest_xray"), image_type="chest_xray"))
        warm = time.perf_counter() - t
    finally:
        shutdown_all()
    raw = medgemma.parse_output(reply, "chest_xray")
    f = medgemma.filter_findings(raw)
    print(f"\nfirst call {first:.1f}s (verify + load + generate), warm call {warm:.1f}s")
    print("raw reply:", reply[:1500])
    print("shown fields:", f.fields, "withheld:", f.withheld_fields, "description withheld:", f.description_withheld, "confidence:", raw.confidence)
    assert isinstance(reply, str) and reply.strip()
