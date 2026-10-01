"""Clinical safety boundary for OCR (docs/14 §5–§6; docs/10). Engines faked; synthetic documents."""

from tests.ocr.test_api import FakeEngines, _attest, _ready_case, _review, _upload, ocr_client  # noqa: F401
from tests.privacy.helpers import auth, token_for, triage


def test_reviewed_ocr_values_never_change_triage_urgency(ocr_client):
    """Same triage input, with and without a confirmed low-Hb / low-platelet report on the case."""
    anm = token_for(ocr_client, "anm")
    plain = _ready_case(ocr_client, anm)
    with_doc = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, with_doc).json()
    _attest(ocr_client, anm, with_doc, doc["document_id"])
    for f in doc["fields"]:
        if f["can_confirm"]:
            assert _review(ocr_client, anm, with_doc, f, "confirmed").status_code == 200
    a = triage(ocr_client, anm, plain)
    b = triage(ocr_client, anm, with_doc)
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    ra, rb = a.json()["result"], b.json()["result"]
    assert ra["urgency"] == rb["urgency"]
    volatile = ("run_id", "case_id", "created_at", "timestamp", "request_id", "evaluated_at")
    assert {k: v for k, v in ra.items() if k not in volatile} == {k: v for k, v in rb.items() if k not in volatile}


def test_comparator_and_unit_survive_storage_and_reload(ocr_client):
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ocr_engines = FakeEngines("two_page_scan")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id, "two_page_scan.pdf").json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    trop = next(f for f in doc["fields"] if f["analyte_key"] == "troponin_i")
    assert _review(ocr_client, anm, case_id, trop, "corrected", {"result": {"value": "0.02", "comparator": "<="}, "unit": "ng/mL"}).status_code == 200
    again = ocr_client.get(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm)).json()
    t = next(f for f in again["fields"] if f["field_id"] == trop["field_id"])
    assert (t["reviewed_value"]["comparator"], t["reviewed_value"]["value"], t["reviewed_value"]["unit"]) == ("<=", "0.02", "ng/mL")
    assert t["value"]["raw"] == "<0.01" and t["value"]["comparator"] == "<"  # machine reading kept, distinguishable
    rv = ocr_client.get(f"/api/v1/cases/{case_id}/documents/reviewed", headers=auth(anm)).json()["values"]
    r = next(v for v in rv if v["field_id"] == trop["field_id"])
    assert r["value"]["comparator"] == "<=" and r["basis"] == "reviewer_entered_from_paper"
    assert r["automatic_checks"].startswith("not applicable")
    assert t["checks_apply_to"] == "machine_reading"


def test_conflict_stays_visible_until_a_human_resolves_it(ocr_client):
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ocr_engines = FakeEngines("cbc_low_platelet", paddle_edit={"85,000": "58,000"})
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    plt = next(f for f in doc["fields"] if f["analyte_key"] == "platelets")
    assert plt["disputed"] and plt["review_status"] == "machine_read"
    rv = ocr_client.get(f"/api/v1/cases/{case_id}/documents/reviewed", headers=auth(anm)).json()
    assert all(v["field_id"] != plt["field_id"] for v in rv["values"])  # never "reviewed" by itself
    _attest(ocr_client, anm, case_id, doc["document_id"])
    reloaded = next(f for f in ocr_client.get(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm)).json()["fields"]
                    if f["field_id"] == plt["field_id"])
    assert reloaded["disputed"] and not reloaded["can_confirm"]
