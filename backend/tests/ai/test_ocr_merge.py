"""Phase 6: reviewed OCR values merged into an AI extraction (docs/16 §1, docs/09 §6.2). OCR engines are replayed
(tests/ocr/test_api.FakeEngines); values are attested and reviewed through the real OCR endpoints. Synthetic only."""

import json

from app.ai.fake_provider import FakeProvider
from tests.ai.helpers import by_field, count, extract
from tests.ocr.test_api import _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import auth, grant, new_case, token_for, triage


def _case_with_reviewed_report(client, anm):
    case_id = new_case(client, anm)
    assert grant(client, anm, case_id, ai=True).status_code == 200
    doc = _upload(client, anm, case_id).json()
    assert _attest(client, anm, case_id, doc["document_id"]).status_code == 200
    hb = next(f for f in doc["fields"] if f["analyte_key"] == "hemoglobin")
    rbc = next(f for f in doc["fields"] if f["analyte_key"] == "rbc")
    assert _review(client, anm, case_id, hb, "confirmed").status_code == 200
    assert _review(client, anm, case_id, rbc, "corrected", {"result": {"value": "4.20"}}).status_code == 200
    return case_id, doc, hb, rbc


def test_reviewed_ocr_values_merge_with_provenance_and_are_never_sent_to_the_provider(ocr_client):  # noqa: F811
    anm = token_for(ocr_client, "anm")
    provider = ocr_client.app.state.ai_provider = FakeProvider()
    case_id, doc, hb, rbc = _case_with_reviewed_report(ocr_client, anm)
    ocr_reviewed = ocr_client.get(f"/api/v1/cases/{case_id}/documents/reviewed", headers=auth(anm)).json()["values"]
    runs_before = count("triage_runs")

    r = extract(ocr_client, anm, case_id, text="Fever for 3 days.")
    assert r.status_code == 201, r.text
    v = r.json()
    f = by_field(v)

    # merged as already human-reviewed values, with the OCR provenance carried over unchanged
    merged = {k: x for k, x in f.items() if x["origin"] == "ocr_reviewed"}
    assert set(merged) == {"ocr:hemoglobin", "ocr:rbc"} == {f"ocr:{x['name']}" for x in ocr_reviewed}
    for x in merged.values():
        assert x["status"] == "human_reviewed" and x["needs_review"] is False and x["agreement"] is None
    src_hb, src_rbc = merged["ocr:hemoglobin"]["evidence"][0]["source"], merged["ocr:rbc"]["evidence"][0]["source"]
    assert src_hb["type"] == "ocr" and src_hb["document_id"] == doc["document_id"] and src_hb["field_id"] == hb["field_id"]
    assert src_hb["regions"] and src_hb["png_sha256"] == hb["page_png_sha256"]
    assert src_rbc["type"] == "ocr_manual_correction" and merged["ocr:rbc"]["value"]["outcome"] == "corrected"
    assert merged["ocr:rbc"]["value"]["value"]["value"] == "4.20"

    # OCR content never reaches the provider: only the typed text was a segment
    assert [s["segment_id"] for s in v["segments"]] == ["S1"]
    sent = json.dumps([call["segments"] for call in provider.calls])
    assert "hemoglobin" not in sent.lower() and "4.20" not in sent and "Fever for 3 days." in sent

    # owned by the document review: cannot be re-reviewed here
    rr = ocr_client.post(f"/api/v1/cases/{case_id}/ai/fields/{merged['ocr:hemoglobin']['field_id']}/review", json={"outcome": "rejected"}, headers=auth(anm))
    assert rr.status_code == 409 and rr.json()["error"]["code"] == "REVIEWED_AT_SOURCE"

    # reviewed view lists them as document-reviewed with no triage-form hints
    rv = ocr_client.get(f"/api/v1/cases/{case_id}/ai/reviewed", headers=auth(anm)).json()
    docvals = [x for x in rv["values"] if x["basis"] == "document_review"]
    assert {x["field"] for x in docvals} == {"ocr:hemoglobin", "ocr:rbc"} and all(x["form_hints"] == [] for x in docvals)

    # note: cited, labelled as document-reviewed; triage safety unchanged
    t = triage(ocr_client, anm, case_id)
    assert t.status_code == 200
    note = ocr_client.post(f"/api/v1/cases/{case_id}/ai/notes", json={"extraction_id": v["extraction_id"]}, headers=auth(anm)).json()
    doc_claims = [c for c in note["claims"] if c["review_state"] == "document_reviewed"]
    assert {c["field_ids"][0] for c in doc_claims} == {merged["ocr:hemoglobin"]["field_id"], merged["ocr:rbc"]["field_id"]}
    assert sorted(c["text"] for c in doc_claims) == ["Document value (reviewed): hemoglobin 11.2 g/dL (printed flag: L).",
                                                       "Document value (reviewed): rbc 4.20 10^6/µL."]
    assert note["urgency"]["recorded_urgency"] == t.json()["result"]["urgency"]
    assert count("triage_runs") == runs_before + 1  # only the human-submitted triage call wrote a run


def test_reviewed_ocr_hb_counts_for_the_maternal_hb_requirement(ocr_client):  # noqa: F811
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ai_provider = FakeProvider()
    case_id = new_case(ocr_client, anm, scenario="maternal")
    grant(ocr_client, anm, case_id, ai=True)
    doc = _upload(ocr_client, anm, case_id).json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    before = extract(ocr_client, anm, case_id, text="BP 120/80.").json()
    assert "hb" in [m["field_name"] for m in before["missing_information"]]  # attested but not yet reviewed: still missing
    hb = next(f for f in doc["fields"] if f["analyte_key"] == "hemoglobin")
    assert _review(ocr_client, anm, case_id, hb, "confirmed").status_code == 200
    after = extract(ocr_client, anm, case_id, text="BP 120/80.").json()
    assert "hb" not in [m["field_name"] for m in after["missing_information"]]


def test_ocr_only_extraction_needs_ai_assist_consent(ocr_client):  # noqa: F811
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ai_provider = FakeProvider()
    case_id = new_case(ocr_client, anm)
    grant(ocr_client, anm, case_id, ai=False)
    r = extract(ocr_client, anm, case_id, text=None)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_ocr_value_text_keeps_comparator_qualitative_unit_and_printed_flag():
    from app.ai.note import _ocr_value_text

    assert _ocr_value_text({"value": "0.02", "comparator": "<=", "qualitative": None, "unit": "ng/mL", "flag": None}) == "<=0.02 ng/mL"
    assert _ocr_value_text({"value": None, "comparator": None, "qualitative": "positive", "unit": None, "flag": "H"}) == "positive (printed flag: H)"
    assert _ocr_value_text({"value": "85000", "comparator": None, "qualitative": None, "unit": "/µL", "flag": "L"}) == "85000 /µL (printed flag: L)"
