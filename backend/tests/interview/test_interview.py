"""Scripted voice interviewer, V1 (docs/plans/scripted-voice-interviewer.md). Synthetic data only.

The script is the fixed question bank for the information the latest triage form is still missing. Patient questions
are spoken; health-worker tasks (measurements, danger-sign screens, lab availability) are shown, never spoken and
never answered here. Answers are confirmed by the visit's ANM, append-only, and never write urgency.
"""

import json
import sqlite3

import pytest

from app.config import get_settings
from app.interview import script
from app.rules.models import TriageInput
from tests.ai.helpers import add_transcript
from tests.privacy.helpers import audit_rows, auth, grant, new_case, token_for, triage, withdraw

API = "/api/v1"


def _rows(sql, *a):
    with sqlite3.connect(get_settings().database_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, a)]


def view(c, tok, cid):
    return c.get(f"{API}/cases/{cid}/interview", headers=auth(tok))


def answer(c, tok, cid, key, **body):
    return c.post(f"{API}/cases/{cid}/interview/answers", json={"question_key": key, "speaker": "patient", "confirm": True, **body}, headers=auth(tok))


@pytest.fixture
def triaged(client):
    """An OPD case whose triage form has vitals and a completed red-flag screen, so the script is patient questions."""
    anm = token_for(client, "anm")
    cid = new_case(client, anm)
    assert grant(client, anm, cid).status_code == 200
    assert triage(client, anm, cid).status_code == 200
    return client, anm, cid


# ── the script (pure) ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_script_is_deterministic_danger_first_and_capped():
    a, b = script.build("opd", None), script.build("opd", None)
    assert a == b and len(a) <= 5
    assert a[0].key == "red_flag_screen" and a[0].audience == "worker" and a[0].is_danger_sign


def test_only_patient_questions_are_spoken_or_answerable_and_measurements_never_are():
    for q in script.build("opd", None) + script.build("maternal", None) + script.build("chronic_ncd", None):
        if q.key in ("spo2", "bp", "bp_reading_1", "bp_reading_2", "pulse", "resp_rate", "temp", "red_flag_screen", "danger_signs", "hb", "blood_sugar"):
            assert q.audience == "worker" and not q.answerable, q.key
        else:
            assert q.audience == "patient" and q.answerable, q.key


def test_the_script_text_comes_only_from_the_question_bank():
    from app.ai.question_bank import QUESTIONS

    for scenario in ("opd", "maternal", "chronic_ncd", "health_camp"):
        for q in script.build(scenario, None):
            assert q.question_text == QUESTIONS[q.key]["question_text"]


def test_the_triage_form_removes_what_it_already_has_but_never_marks_a_screen_done_by_itself():
    inp = TriageInput.model_validate({"scenario": "opd", "red_flag_screen_completed": True,
                                      "vitals": {"spo2": 97, "pulse": 80, "sbp": 120, "dbp": 80}})
    keys = [q.key for q in script.build("opd", inp)]
    assert keys == ["chief_complaint", "duration", "severity"]
    not_done = TriageInput.model_validate({"scenario": "opd", "vitals": {"spo2": 97}})
    assert script.build("opd", not_done)[0].key == "red_flag_screen"


# ── the API ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_anm_sees_the_script_and_records_a_confirmed_choice_and_text_answer(triaged):
    c, anm, cid = triaged
    v = view(c, anm, cid).json()
    assert [q["key"] for q in v["questions"]] == ["chief_complaint", "duration", "severity"]
    assert v["still_missing"] == ["chief_complaint", "duration", "severity"]
    assert answer(c, anm, cid, "duration", choice="1-3 days").status_code == 201
    assert answer(c, anm, cid, "chief_complaint", text="cough and mild fever").status_code == 201
    v = view(c, anm, cid).json()
    by = {q["key"]: q for q in v["questions"]}
    assert by["duration"]["answer"]["choice"] == "1-3 days" and by["duration"]["answer"]["speaker"] == "patient"
    assert by["chief_complaint"]["answer"]["text"] == "cough and mild fever"
    assert v["still_missing"] == ["severity"]


def test_not_sure_is_never_a_negative_and_stays_missing(client):
    anm = token_for(client, "anm")
    cid = new_case(client, anm, "chronic_ncd")
    assert grant(client, anm, cid).status_code == 200
    assert triage(client, anm, cid, scenario="chronic_ncd").status_code == 200  # screen done, first BP reading on the form
    keys = [q["key"] for q in view(client, anm, cid).json()["questions"]]
    assert "adherence" in keys and "bp_reading_1" not in keys
    assert answer(client, anm, cid, "adherence", choice="not sure").status_code == 201
    v = view(client, anm, cid).json()
    assert {q["key"]: q for q in v["questions"]}["adherence"]["answer"]["is_unknown"] is True
    assert "adherence" in v["still_missing"]  # "not sure" is recorded, but the information is still missing
    assert answer(client, anm, cid, "adherence", choice="no doses missed").status_code == 201
    assert "adherence" not in view(client, anm, cid).json()["still_missing"]


def test_answers_are_validated_and_must_be_confirmed(triaged):
    c, anm, cid = triaged
    bad = [
        {"question_key": "duration", "choice": "forever"},  # not one of the fixed options
        {"question_key": "duration", "text": "three days"},  # a choice question takes a choice
        {"question_key": "chief_complaint", "choice": "1-3 days"},  # a text question takes text
        {"question_key": "chief_complaint", "text": "x" * 501},
        {"question_key": "chief_complaint", "text": "   "},
        {"question_key": "spo2", "text": "97"},  # a measurement task is never answered here
        {"question_key": "red_flag_screen", "choice": "none present"},  # nor is a danger-sign screen
        {"question_key": "not_a_question", "text": "x"},
    ]
    for b in bad:
        r = c.post(f"{API}/cases/{cid}/interview/answers", json={"speaker": "patient", "confirm": True, **b}, headers=auth(anm))
        assert r.status_code in (400, 409), (b, r.text)
    r = c.post(f"{API}/cases/{cid}/interview/answers", json={"question_key": "duration", "choice": "1-3 days", "speaker": "patient"}, headers=auth(anm))
    assert r.status_code == 400  # confirm is required
    assert _rows("SELECT COUNT(*) AS n FROM interview_answer_events")[0]["n"] == 0


def test_a_transcript_source_must_be_this_cases_and_the_span_must_fit(triaged):
    c, anm, cid = triaged
    tid = add_transcript(cid, "It started three days ago with a cough.")
    ok = answer(c, anm, cid, "chief_complaint", text="a cough", source={"transcription_id": tid, "char_start": 31, "char_end": 38})
    assert ok.status_code == 201, ok.text
    other = new_case(c, anm)
    grant(c, anm, other)
    foreign = add_transcript(other, "headache")
    assert answer(c, anm, cid, "chief_complaint", text="headache", source={"transcription_id": foreign, "char_start": 0, "char_end": 8}).status_code == 404
    assert answer(c, anm, cid, "chief_complaint", text="x", source={"transcription_id": tid, "char_start": 30, "char_end": 999}).status_code == 400


def test_answers_are_append_only_and_the_latest_counts(triaged):
    c, anm, cid = triaged
    answer(c, anm, cid, "duration", choice="1-3 days")
    answer(c, anm, cid, "duration", choice="4-7 days")
    assert {q["key"]: q for q in view(c, anm, cid).json()["questions"]}["duration"]["answer"]["choice"] == "4-7 days"
    with sqlite3.connect(get_settings().database_path) as db:
        for sql in ("UPDATE interview_answer_events SET choice_value = 'x'", "DELETE FROM interview_answer_events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(sql)


# ── safety ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_interview_never_writes_urgency_or_completes_a_screen(client):
    anm = token_for(client, "anm")
    cid = new_case(client, anm)
    grant(client, anm, cid)
    before = _rows("SELECT COUNT(*) AS n FROM triage_runs")[0]["n"]
    v = view(client, anm, cid).json()
    assert v["questions"][0]["key"] == "red_flag_screen" and v["questions"][0]["answerable"] is False
    assert answer(client, anm, cid, "chief_complaint", text="chest pain since morning").status_code == 201
    assert _rows("SELECT COUNT(*) AS n FROM triage_runs")[0]["n"] == before
    assert "red_flag_screen" in view(client, anm, cid).json()["still_missing"]


def test_who_may_record_and_who_may_read(triaged):
    c, anm, cid = triaged
    mo, sup, other = token_for(c, "mo"), token_for(c, "supervisor"), token_for(c, "anm_other")
    answer(c, anm, cid, "chief_complaint", text="cough")
    assert answer(c, mo, cid, "duration", choice="1-3 days").status_code == 403
    assert answer(c, sup, cid, "duration", choice="1-3 days").status_code == 403
    assert answer(c, other, cid, "duration", choice="1-3 days").status_code == 404
    assert view(c, mo, cid).json()["questions"][0]["answer"]["text"] == "cough"
    s = view(c, sup, cid).json()
    assert s["answers_hidden"] is True and all(q["answer"] is None for q in s["questions"]) and "cough" not in json.dumps(s)
    assert view(c, other, cid).status_code == 404


def test_withdrawing_triage_consent_stops_recording_and_hides_answers(triaged):
    c, anm, cid = triaged
    answer(c, anm, cid, "chief_complaint", text="cough")
    assert withdraw(c, anm, cid, "triage").status_code == 200
    assert answer(c, anm, cid, "duration", choice="1-3 days").status_code == 403
    v = view(c, anm, cid)
    assert v.status_code == 403 and "cough" not in v.text


def test_the_audit_log_gets_keys_and_enums_but_never_the_answer(triaged, caplog):
    import logging

    c, anm, cid = triaged
    with caplog.at_level(logging.DEBUG):
        answer(c, anm, cid, "chief_complaint", text="synthetic-secret-complaint")
    rows = [r for r in audit_rows(c) if r["action"] == "interview_answer_recorded"]
    assert len(rows) == 1 and json.loads(rows[0]["details_json"]) == {"question_key": "chief_complaint", "kind": "text", "source": "typed",
                                                                       "speaker": "patient", "is_unknown": False}
    assert "synthetic-secret-complaint" not in json.dumps(audit_rows(c)) + " ".join(r.getMessage() for r in caplog.records)


def test_a_text_answer_with_a_phone_number_or_name_is_refused(triaged):
    """The same check as review reasons. Uncued lowercase or bare Indian names can still slip through: a documented
    weakness of the scrubber (tests/privacy/test_pii.py known misses), not a guarantee."""
    c, anm, cid = triaged
    for text in ("call me on 9876543210", "Patient Ramesh Kumar has a cough"):
        r = answer(c, anm, cid, "chief_complaint", text=text)
        assert r.status_code == 422 and r.json()["error"]["code"] == "PII_DETECTED" and text not in r.text
    assert _rows("SELECT COUNT(*) AS n FROM interview_answer_events")[0]["n"] == 0
