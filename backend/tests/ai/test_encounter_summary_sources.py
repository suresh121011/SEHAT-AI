"""Encounter summary: medical images, interview answers, the visit header and the sectioned note
(docs/plans/summary-note-all-sources.md, S1-S4). Synthetic data only; OCR replayed, image backend stubbed, AI = fake.

Image findings are model output no rule validates: they stay in the Imaging rows and never in a written sentence
(owner decision D2a). Interview answers are a table, never prose (I4). Urgency is never written here."""

import pytest

from app.ai import encounter_summary as es
from app.ai.fake_provider import FakeProvider
from tests.ai.helpers import count, extract
from tests.interview.test_interview import answer
from tests.ocr.test_api import _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import auth, grant, new_case, token_for, triage
from tests.test_medgemma import FX, StubBackend, _enable, _img

API = "/api/v1"
URL = API + "/cases/{}/encounter-summary"


def _generate(c, token, case_id):
    r = c.post(URL.format(case_id), headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def _prose(s) -> str:
    return " ".join(n["text"] for n in s["narrative"]).lower()


@pytest.fixture
def case(ocr_client, monkeypatch):  # noqa: F811
    _enable(monkeypatch)
    ocr_client.app.state.ai_provider = FakeProvider()
    anm, mo = token_for(ocr_client, "anm"), token_for(ocr_client, "mo")
    case_id = new_case(ocr_client, anm)
    assert grant(ocr_client, anm, case_id, ai=True).status_code == 200
    return ocr_client, anm, mo, case_id


# ── S1: medical images ────────────────────────────────────────────────────────────────────────────────────


def test_a_described_xray_is_listed_with_its_findings_as_rows_and_never_in_a_sentence(case):
    c, anm, mo, case_id = case
    img = _img(c, anm, case_id, "chest_xray").json()
    assert img["status"] == "described" and img["structured_fields"]
    s = _generate(c, mo, case_id)["summary"]
    assert len(s["imaging"]) == 1
    f = s["facts"][s["imaging"][0]]
    assert f["section"] == "imaging" and f["provenance"] == "image_described_unacknowledged" and f["trust"] == "unchecked"
    assert f["value"]["image_type"] == "chest_xray" and f["value"]["structured_fields"] == img["structured_fields"]
    assert f["value"]["source_image_url"] == img["source_image_url"] and f["sources"][0]["document_id"] == img["document_id"]
    # the sentence names the image and its review state only: no finding word reaches the prose or the fact's display
    finding_words = [w for v in img["structured_fields"].values() for w in str(v).lower().split() if len(w) > 5]
    assert finding_words and not any(w in _prose(s) or w in f["display"].lower() for w in finding_words)
    sent = [n for n in s["narrative"] if n["section"] == "imaging"]
    assert len(sent) == 1 and "chest x-ray" in sent[0]["text"].lower() and "not yet reviewed" in sent[0]["text"]
    assert not [b for b in s["blocked"] if b["section"] == "imaging"]
    # canned demo text is never mistaken for a model's reading of the image
    assert f["value"]["described_by"]["synthetic"] is True
    details = [x["text"] for sec in s["note_sections"] if sec["key"] == "imaging" for x in sec["lines"] if x["kind"] == "detail"]
    assert details[0].startswith("DEMO TEXT from the built-in fake backend") and "DEMO TEXT" in s["note_text"]


def test_a_flagged_image_needs_attention_until_acknowledged_and_any_image_change_marks_the_summary_stale(case):
    c, anm, mo, case_id = case
    first = _generate(c, mo, case_id)
    assert first["summary"]["imaging"] == []
    c.app.state.medgemma_backend = StubBackend(FX["pneumothorax"]["output"])
    img = _img(c, anm, case_id, "chest_xray").json()
    assert img["requires_acknowledgement"] is True
    assert c.get(URL.format(case_id), headers=auth(mo)).json()["is_stale"] is True  # an upload is new evidence

    second = _generate(c, mo, case_id)
    s = second["summary"]
    assert [a["kind"] for a in s["attention"] if a["kind"] == "image_flag_unacknowledged"] == ["image_flag_unacknowledged"]
    assert any(p["kind"] == "medical_image" and p["ref"] == img["document_id"] for p in s["pending_review"])
    f = s["facts"][s["imaging"][0]]
    assert f["value"]["flags"] and "pneumothorax" not in _prose(s)

    assert c.post(f"{API}/cases/{case_id}/medical-images/{img['document_id']}/acknowledge", json={}, headers=auth(mo)).status_code == 200
    assert c.get(URL.format(case_id), headers=auth(mo)).json()["is_stale"] is True  # the acknowledgement changes the summary
    s = _generate(c, mo, case_id)["summary"]
    f = s["facts"][s["imaging"][0]]
    assert f["provenance"] == "image_described_acknowledged" and f["trust"] == "checked"
    assert not [a for a in s["attention"] if a["kind"] == "image_flag_unacknowledged"]
    assert not [p for p in s["pending_review"] if p["kind"] == "medical_image"]

    assert c.delete(f"{API}/cases/{case_id}/medical-images/{img['document_id']}", headers=auth(mo)).status_code in (200, 204)
    assert c.get(URL.format(case_id), headers=auth(mo)).json()["is_stale"] is True
    assert _generate(c, mo, case_id)["summary"]["imaging"] == []  # a deleted image is not shown


def test_an_image_that_was_not_described_is_listed_as_on_file_without_findings(ocr_client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    anm = token_for(ocr_client, "anm")
    case_id = new_case(ocr_client, anm)
    assert grant(ocr_client, anm, case_id).status_code == 200
    assert _img(ocr_client, anm, case_id, "chest_xray").json()["status"] == "not_available"
    s = _generate(ocr_client, anm, case_id)["summary"]
    f = s["facts"][s["imaging"][0]]
    assert f["provenance"] == "image_on_file" and f["value"]["structured_fields"] == {} and f["value"]["description"] is None
    assert "no ai description" in [n for n in s["narrative"] if n["section"] == "imaging"][0]["text"].lower()


def test_without_ai_assist_consent_the_image_is_listed_but_its_ai_description_is_left_out(ocr_client, monkeypatch):  # noqa: F811
    _enable(monkeypatch)
    anm = token_for(ocr_client, "anm")
    case_id = new_case(ocr_client, anm)
    assert grant(ocr_client, anm, case_id, ai=True).status_code == 200
    assert _img(ocr_client, anm, case_id, "chest_xray").json()["status"] == "described"
    assert ocr_client.post(f"{API}/cases/{case_id}/consent/withdraw", json={"purpose": "ai_assist"}, headers=auth(anm)).status_code == 200
    s = _generate(ocr_client, anm, case_id)["summary"]
    f = s["facts"][s["imaging"][0]]
    assert f["provenance"] == "image_on_file" and f["value"]["structured_fields"] == {}
    assert {"source": "image_descriptions", "reason": "ai_assist_consent_not_in_effect"} in s["excluded_sources"]


# ── S2: interview answers ─────────────────────────────────────────────────────────────────────────────────


def test_interview_answers_are_a_table_never_prose_and_an_answered_question_is_no_longer_missing(case):
    c, anm, mo, case_id = case
    assert triage(c, anm, case_id).status_code == 200
    first = _generate(c, mo, case_id)
    assert {"chief_complaint", "duration", "severity"} <= {m["key"] for m in first["summary"]["missing_information"]}

    assert answer(c, anm, case_id, "chief_complaint", text="burning when passing urine").status_code == 201
    assert answer(c, anm, case_id, "duration", choice="1-3 days", speaker="caregiver").status_code == 201
    assert c.get(URL.format(case_id), headers=auth(mo)).json()["is_stale"] is True  # a new answer is new evidence

    s = _generate(c, mo, case_id)["summary"]
    rows = {s["facts"][i]["value"]["question_key"]: s["facts"][i] for i in s["interview"]}
    assert set(rows) == {"chief_complaint", "duration"}
    cc = rows["chief_complaint"]
    assert cc["display"] == "burning when passing urine" and cc["provenance"] == "interview_confirmed" and cc["trust"] == "checked"
    assert cc["label"] == "What is the main problem today?" and cc["sources"][0]["type"] == "interview_answer"
    assert rows["duration"]["value"]["speaker"] == "caregiver" and rows["duration"]["sources"][0]["recorded_by_role"] == "anm"
    assert "burning" not in _prose(s) and not [n for n in s["narrative"] if n["section"] == "interview"]  # I4: table only
    missing = {m["key"] for m in s["missing_information"]}
    assert "chief_complaint" not in missing and "duration" not in missing and "severity" in missing


def test_a_not_sure_answer_stays_missing():
    f = es.Fact(fact_id="F1", section="interview", key="answer:adherence", label="q", display="not sure", value={"question_key": "adherence", "is_unknown": True},
                provenance="interview_confirmed", trust="checked", sources=[{"type": "interview_answer"}], flags=["unknown_answer"])
    g = es.Fact(fact_id="F2", section="interview", key="answer:duration", label="q", display="1-3 days", value={"question_key": "duration", "is_unknown": False},
                provenance="interview_confirmed", trust="checked", sources=[{"type": "interview_answer"}])
    keys = es.present_keys([f, g], None)
    assert "adherence" not in keys and "duration" in keys


# ── S3 + S4: header and the sectioned note ──────────────────────────────────────────────────────────────────


def test_header_and_note_cover_every_recorded_fact_once_in_clinical_order(case):
    c, anm, mo, case_id = case
    assert extract(c, anm, case_id, "Fever for 3 days with cough. No chest pain. Taking paracetamol 500 mg twice daily.").status_code == 201
    doc = _upload(c, anm, case_id).json()
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    fields = c.get(f"{API}/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm)).json()["fields"]
    assert _review(c, anm, case_id, fields[0], "confirmed").status_code == 200
    _img(c, anm, case_id, "chest_xray")
    assert c.put(f"{API}/cases/{case_id}/body-map", json={"regions": ["chest_right"]}, headers=auth(anm)).status_code == 200
    assert triage(c, anm, case_id).status_code == 200
    assert answer(c, anm, case_id, "severity", choice="6").status_code == 201

    s = _generate(c, mo, case_id)["summary"]
    assert s["schema_version"] == es.SUMMARY_SCHEMA_VERSION and s["schema_version"].endswith(".4")
    h = s["header"]
    assert h["scenario"] == "opd" and h["facility_code"] == "PHC-KHURDA-01" and h["visit_started_at"] and h["patient_record_linked"] is False

    keys = [x["key"] for x in s["note_sections"]]
    order = [k for k in es.NOTE_ORDER if k in keys]
    assert keys == order and keys[0] == "patient_visit" and keys[1] == "urgency" and keys[-1] == "sign_off"
    assert {"presenting_problem", "history", "interview", "measurements", "labs", "imaging", "medications", "red_flags"} <= set(keys)

    cited = [i for sec in s["note_sections"] for line in sec["lines"] for i in line["fact_ids"]]
    assert set(cited) == set(s["facts"]), "every recorded fact appears in the note"
    # a value never appears twice as a statement: once in a sentence, or once as a row
    stated = [i for sec in s["note_sections"] for line in sec["lines"] if line["kind"] in ("sentence", "row") for i in line["fact_ids"]]
    assert len(stated) == len(set(stated))
    # rows carry how far the value was checked; unchecked values are never shown bare
    for sec in s["note_sections"]:
        for line in sec["lines"]:
            if line["kind"] == "row" and any(s["facts"][i]["trust"] == "unchecked" for i in line["fact_ids"]):
                assert "not yet" in line["text"].lower(), line["text"]

    text = s["note_text"]
    assert text.startswith("ENCOUNTER SUMMARY") and es.DISCLAIMER in text and "DRAFT" in text
    for title in (x["title"] for x in s["note_sections"]):
        assert title.upper() in text
    assert "Urgency (rules engine)" in text and s["urgency"]["recorded_urgency"] in text


def test_note_says_plainly_when_nothing_is_recorded_and_no_triage_was_run(case):
    c, anm, mo, case_id = case
    s = _generate(c, mo, case_id)["summary"]
    keys = [x["key"] for x in s["note_sections"]]
    assert "presenting_problem" not in keys and "imaging" not in keys  # empty sections are left out
    urgency = next(x for x in s["note_sections"] if x["key"] == "urgency")
    assert "not determined" in urgency["lines"][0]["text"].lower()
    assert "missing_information" in keys


def test_the_summary_still_writes_no_triage_or_review_rows(case):
    c, anm, mo, case_id = case
    assert triage(c, anm, case_id).status_code == 200
    _img(c, anm, case_id, "chest_xray")
    answer(c, anm, case_id, "chief_complaint", text="cough")
    before = (count("triage_runs", case_id), count("review_events", case_id), count("interview_answer_events", case_id), count("medical_image_acknowledgements"))
    _generate(c, mo, case_id)
    assert (count("triage_runs", case_id), count("review_events", case_id), count("interview_answer_events", case_id), count("medical_image_acknowledgements")) == before


def test_new_provenances_are_in_the_single_registry_with_the_right_rules():
    p = es.PROVENANCE
    assert p["image_described_unacknowledged"]["trust"] == "unchecked" and p["image_described_unacknowledged"]["tag"]
    assert p["image_described_acknowledged"]["trust"] == "checked" and p["image_on_file"]["trust"] == "entered"
    assert p["interview_confirmed"]["narrative"] is False  # I4: interview answers never reach prose


def test_an_interview_duration_that_contradicts_the_recorded_one_is_a_conflict_and_neither_is_narrated(case):
    c, anm, mo, case_id = case
    assert extract(c, anm, case_id, "Fever for 3 days.").status_code == 201
    assert triage(c, anm, case_id).status_code == 200
    assert answer(c, anm, case_id, "duration", choice="more than 1 week").status_code == 201
    s = _generate(c, mo, case_id)["summary"]
    conflict = [x for x in s["conflicts"] if x["key"] == "duration_interview"]
    assert len(conflict) == 1 and {v["display"] for v in conflict[0]["values"]} == {"3 days", "more than 1 week"}
    assert "3 days" not in _prose(s) and "more than 1 week" not in _prose(s)
    assert any(a["kind"] == "conflict" for a in s["attention"])
    line = [x for sec in s["note_sections"] for x in sec["lines"] if x["kind"] == "conflict"]
    assert len(line) == 1 and "no value chosen" in line[0]["text"]


def test_an_interview_duration_inside_its_range_or_against_an_approximate_value_is_not_a_conflict():
    def fact(fid, section, key, display, value, prov):
        return es.Fact(fact_id=fid, section=section, key=key, label="Duration", display=display, value=value, provenance=prov,
                       trust=es.PROVENANCE[prov]["trust"], sources=[{"type": "test"}])
    ans = lambda choice, unknown=False: fact("A", "interview", "answer:duration", choice, {"question_key": "duration", "is_unknown": unknown}, "interview_confirmed")  # noqa: E731
    dur = lambda text: fact("D", "timeline", "duration", text, {"text": text, "parsed": es.parse_duration(text)}, "ai_accepted")  # noqa: E731
    assert es.interview_duration_conflicts([ans("1-3 days"), dur("3 days")]) == []
    assert es.interview_duration_conflicts([ans("more than 1 week"), dur("1 week")]) == []  # boundary: never a conflict
    assert es.interview_duration_conflicts([ans("less than 1 day"), dur("6 hours")]) == []
    assert es.interview_duration_conflicts([ans("more than 1 week"), dur("about 3 days")]) == []  # approximate: not compared
    assert es.interview_duration_conflicts([ans("more than 1 week"), dur("a while")]) == []  # unparsed: not compared
    assert es.interview_duration_conflicts([ans("not sure", True), dur("3 days")]) == []
    assert len(es.interview_duration_conflicts([ans("less than 1 day"), dur("2 weeks")])) == 1
