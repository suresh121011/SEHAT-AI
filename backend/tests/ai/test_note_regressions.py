"""Regressions found while building the encounter summary (docs/19 §2). Synthetic data only.

R9: a note over an unreviewed keyword red flag raised KeyError (500): the tag map had no entry for that state.
R4: a reviewed OCR prescription line lost its strength, dose pattern, frequency and duration in the note.
Also: reviewers can now correct a prescription row's frequency and duration (closed vocabularies, no free text)."""

from app.ai import note
from app.ai.fake_provider import FakeProvider
from tests.ai.helpers import extract
from tests.ocr.test_api import FakeEngines, _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import auth, grant, new_case, token_for, triage


def test_note_over_an_unreviewed_keyword_red_flag_is_drafted_and_tagged(ai_client):
    anm = token_for(ai_client, "anm")
    case_id = new_case(ai_client, anm)
    grant(ai_client, anm, case_id, ai=True)
    view = extract(ai_client, anm, case_id, text="Needle prick injury from a used syringe 20 minutes ago.").json()
    assert any(f["origin"] == "keyword_rule" for f in view["fields"])
    r = ai_client.post(f"/api/v1/cases/{case_id}/ai/notes", json={"extraction_id": view["extraction_id"]}, headers=auth(anm))
    assert r.status_code == 201, r.text
    kw = [c for c in r.json()["claims"] if c["review_state"] == "unreviewed_keyword_suggestion"]
    assert kw and "[unreviewed keyword match]" in r.json()["summary"]


def test_every_note_review_state_has_a_tag():
    states = {"reviewed", "document_reviewed", "unreviewed_ai_extracted", "unreviewed_keyword_suggestion"}
    assert states <= set(note._TAGS)


def test_ocr_medication_claim_keeps_strength_pattern_frequency_and_duration():
    f = {"kind": "ocr_medication", "field": "ocr:Paracetamol"}
    v = {"name": "Paracetamol", "value": {"drug_raw": "Paracetamol", "strength_raw": "650 mg", "dosage_pattern": "1-0-1", "frequency_raw": None,
                                          "duration_raw": "5 days"}}
    assert note.claim_text(f, v) == "Documented on prescription (reviewed): Paracetamol 650 mg 1-0-1 for 5 days."


def _rx_case(c, anm):
    c.app.state.ocr_engines = FakeEngines("rx_handwritten")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id, ai=True).status_code == 200
    doc = _upload(c, anm, case_id, "rx_handwritten.png", "prescription").json()
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    return case_id, {f["drug_raw"]: f for f in doc["fields"] if f["kind"] == "medication"}


def test_reviewed_prescription_reaches_the_note_with_its_details(ocr_client):  # noqa: F811
    anm = token_for(ocr_client, "anm")
    ocr_client.app.state.ai_provider = FakeProvider()
    case_id, meds = _rx_case(ocr_client, anm)
    assert _review(ocr_client, anm, case_id, meds["Pantoprazole"], "confirmed").status_code == 200
    v = extract(ocr_client, anm, case_id, text="Fever for 3 days.").json()
    assert triage(ocr_client, anm, case_id).status_code == 200
    n = ocr_client.post(f"/api/v1/cases/{case_id}/ai/notes", json={"extraction_id": v["extraction_id"]}, headers=auth(anm)).json()
    texts = [c["text"] for c in n["claims"] if c["kind"] == "ocr_medication"]
    assert texts == ["Documented on prescription (reviewed): Pantoprazole 40 mg 1-0-0."]


def test_prescription_frequency_and_duration_can_be_corrected_with_closed_values_only(ocr_client):  # noqa: F811
    anm = token_for(ocr_client, "anm")
    case_id, meds = _rx_case(ocr_client, anm)
    para = meds["Paracetamol"]
    ok = _review(ocr_client, anm, case_id, para, "corrected", {"frequency_raw": "BD", "duration_raw": "5 days"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["reviewed_value"]["frequency_raw"] == "BD" and ok.json()["reviewed_value"]["duration_raw"] == "5 days"
    pant = meds["Pantoprazole"]
    for bad in ({"frequency_raw": "twice a day maybe"}, {"duration_raw": "until better"}, {"frequency_raw": "BD", "drug_raw": "Other"}):
        r = _review(ocr_client, anm, case_id, pant, "corrected", bad)
        assert r.status_code in (400, 422), (bad, r.text)
