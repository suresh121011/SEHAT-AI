"""Per-symptom structure (docs/plans/clinical-note-v2-plan.md, step 1; owner decision N1, 2026-11-11).

Each symptom may carry its own duration and a normalised term from a closed list. The patient's words stay the
name; the term is a label for the reviewer, never free text. A duration is kept only when its words are in the
symptom's own quote; its number only when that number is in the quote. Synthetic data only; fake provider or
hand-written model output; no model runs here."""

import pytest

from app.ai import encounter_summary as es
from app.ai import fake_provider
from app.ai.fake_provider import FakeProvider
from app.ai.grounding import ground
from app.ai.schemas import SYMPTOM_TERMS, ExtractionOutput
from tests.ai.helpers import by_field, extract
from tests.privacy.helpers import auth, grant, new_case, token_for, triage

URL = "/api/v1/cases/{}/encounter-summary"


def _out(symptoms, duration=None):
    return ExtractionOutput.model_validate({"chief_complaint": None, "onset": None, "duration": duration, "symptoms": symptoms, "measurements": [],
                                            "medications": [], "red_flags": [], "urgency_suggestion": None})


def _sym(name, quote, **extra):
    return {"name": name, "negated": False, "evidence": [{"segment_id": "S1", "quote": quote}], **extra}


def _entries(text, symptoms):
    g = ground({"S1": text}, _out(symptoms))
    return {e.key: e for e in g.entries}, g.dropped


# ── schema ──────────────────────────────────────────────────────────────────────────────────────────────


def test_old_shape_still_validates_and_the_term_is_a_closed_list():
    assert _out([_sym("fever", "fever")]).symptoms[0].duration is None
    assert "headache" in SYMPTOM_TERMS and "fever" in SYMPTOM_TERMS
    with pytest.raises(Exception):
        _out([_sym("head is paining", "head is paining", term="migraine with aura")])  # not in the list: refused, never stored
    with pytest.raises(Exception):
        _out([_sym("fever", "fever", duration_unit="fortnight")])


# ── grounding ───────────────────────────────────────────────────────────────────────────────────────────


def test_each_symptom_keeps_its_own_duration_and_the_patients_words():
    text = "I am suffering from fever since 2 days and my head is paining since 2 hours."
    entries, dropped = _entries(text, [
        _sym("fever", "fever since 2 days", term="fever", duration="2 days", duration_amount=2, duration_unit="day"),
        _sym("head is paining", "my head is paining since 2 hours", term="headache", duration="2 hours", duration_amount=2, duration_unit="hour"),
    ])
    assert dropped == []
    assert entries["symptom:fever"].value == {"name": "fever", "negated": False, "duration": {"text": "2 days", "amount": 2.0, "unit": "day"}}
    head = entries["symptom:headache"].value  # keyed by the term, so passes and languages that word it differently agree
    assert head["name"] == "head is paining" and head["term"] == "headache" and head["duration"] == {"text": "2 hours", "amount": 2.0, "unit": "hour"}


def test_a_duration_that_is_not_in_the_symptoms_own_quote_is_dropped_and_the_symptom_is_kept():
    text = "Fever since 2 days. Headache today."
    entries, dropped = _entries(text, [_sym("headache", "Headache today", duration="2 days", duration_amount=2, duration_unit="day")])
    assert entries["symptom:headache"].value == {"name": "headache", "negated": False}
    assert dropped == [{"field": "symptom:headache", "reason": "symptom_duration_not_in_quote"}]


def test_a_number_the_quote_does_not_contain_is_dropped_but_the_words_stay():
    text = "do din se bukhar hai"
    entries, dropped = _entries(text, [_sym("bukhar", "do din se bukhar", term="fever", duration="do din", duration_amount=2, duration_unit="day")])
    assert entries["symptom:fever"].value["duration"] == {"text": "do din", "amount": None, "unit": None}
    assert {"field": "symptom:fever", "reason": "symptom_duration_number_not_in_quote"} in dropped
    assert "duration_number_unverified" in entries["symptom:fever"].flags


def test_hindi_number_words_in_the_quote_verify_the_number():
    text = "मुझे दो दिन से बुखार है और दो घंटे से सिर में दर्द है"
    entries, dropped = _entries(text, [
        _sym("बुखार", "दो दिन से बुखार", term="fever", duration="दो दिन", duration_amount=2, duration_unit="day"),
        _sym("सिर में दर्द", "दो घंटे से सिर में दर्द", term="headache", duration="दो घंटे", duration_amount=2, duration_unit="hour"),
    ])
    assert dropped == []
    assert entries["symptom:fever"].value["duration"]["amount"] == 2.0 and entries["symptom:headache"].value["duration"]["unit"] == "hour"


def test_a_denied_symptom_never_carries_a_duration():
    text = "No fever for 2 days."
    entries, dropped = _entries(text, [{**_sym("fever", "No fever for 2 days", duration="2 days", duration_amount=2, duration_unit="day"), "negated": True}])
    assert "duration" not in entries["symptom:fever"].value and entries["symptom:fever"].value["negated"] is True


def test_passes_that_disagree_on_a_duration_do_not_vote_the_same():
    text = "fever since 2 days"
    a, _ = _entries(text, [_sym("fever", text, duration="2 days", duration_amount=2, duration_unit="day")])
    b, _ = _entries(text, [_sym("fever", text)])
    assert a["symptom:fever"].vote != b["symptom:fever"].vote


# ── fake provider (the keyword extractor used in tests and the corpus) ─────────────────────────────────────


def test_fake_extractor_reads_a_duration_per_symptom_and_maps_everyday_words_to_a_term():
    out = fake_provider.extract((("S1", "I am suffering from fever since 2 days and my head is paining since 2 hours. No vomiting."),))
    got = {s.get("term") or s["name"]: s for s in out["symptoms"]}
    assert got["fever"]["duration"] == "2 days" and (got["fever"]["duration_amount"], got["fever"]["duration_unit"]) == (2, "day")
    assert got["headache"]["name"] == "head is paining" and (got["headache"]["duration_amount"], got["headache"]["duration_unit"]) == (2, "hour")
    assert got["vomiting"]["negated"] is True and got["vomiting"].get("duration") is None
    ExtractionOutput.model_validate(out)  # the fake's output is schema-valid


def test_fake_extractor_does_not_guess_which_symptom_a_shared_duration_belongs_to():
    out = fake_provider.extract((("S1", "Fever and cough for 3 days."),))
    got = {s["name"]: s for s in out["symptoms"]}
    assert got["fever"].get("duration") is None and got["cough"].get("duration") is None
    assert out["duration"]["value"] == "3 days"  # the visit-level duration still carries it
    one = fake_provider.extract((("S1", "Fever for three days with cough."),))
    got = {s["name"]: s for s in one["symptoms"]}
    assert got["fever"]["duration"] == "three days" and got["fever"]["duration_amount"] == 3 and got["cough"].get("duration") is None


# ── API: extraction → review → summary ────────────────────────────────────────────────────────────────────


@pytest.fixture
def case(ai_client):
    anm = token_for(ai_client, "anm")
    case_id = new_case(ai_client, anm)
    assert grant(ai_client, anm, case_id, ai=True).status_code == 200
    return ai_client, anm, case_id


def _summary(c, tok, case_id):
    r = c.post(URL.format(case_id), headers=auth(tok))
    assert r.status_code == 201, r.text
    return r.json()["summary"]


def test_the_summary_states_each_symptom_with_its_own_duration_and_raises_no_conflict(case):
    c, anm, case_id = case
    ext = extract(c, anm, case_id, text="I am suffering from fever since 2 days and my head is paining since 2 hours. No vomiting.")
    assert ext.status_code == 201, ext.text
    fields = by_field(ext.json())
    assert fields["symptom:fever"]["value"]["duration"] == {"text": "2 days", "amount": 2, "unit": "day"}
    assert fields["symptom:headache"]["value"]["term"] == "headache"
    assert triage(c, anm, case_id).status_code == 200
    s = _summary(c, anm, case_id)
    prose = " ".join(n["text"] for n in s["narrative"])
    assert "Fever for 2 days [not yet checked] (as reported)." in prose and "Headache for 2 hours [not yet checked] (as reported)." in prose
    assert "Duration:" not in prose  # the visit-level duration is not stated a second time
    assert s["conflicts"] == []
    assert "duration" not in {m["key"] for m in s["missing_information"]}
    present = {s["facts"][i]["display"] for i in s["symptoms"]["present"]}
    assert present == {"fever", "headache"}  # the normalised term is shown; the words said stay in the value
    head = next(s["facts"][i] for i in s["symptoms"]["present"] if s["facts"][i]["display"] == "headache")
    assert head["value"]["name"] == "head is paining" and head["label"] == "headache (said: head is paining)"
    durs = {s["facts"][i]["key"]: s["facts"][i] for i in s["timeline"]}
    assert set(durs) == {"duration:fever", "duration:headache"}
    assert durs["duration:headache"]["value"]["linked_symptom"] == "headache" and durs["duration:headache"]["sources"][0]["type"] == "ai_field"


def test_a_reviewer_can_correct_one_symptoms_duration_and_the_summary_uses_it(case):
    c, anm, case_id = case
    f = by_field(extract(c, anm, case_id, text="Fever since 2 days.").json())["symptom:fever"]
    url = f"/api/v1/cases/{case_id}/ai/fields/{f['field_id']}/review"
    bad = c.post(url, json={"outcome": "corrected", "corrected": {"negated": False, "duration_amount": 5}}, headers=auth(anm))
    assert bad.status_code == 422  # an amount needs its unit
    ok = c.post(url, json={"outcome": "corrected", "corrected": {"negated": False, "duration_amount": 5, "duration_unit": "day"}}, headers=auth(anm))
    assert ok.status_code == 200, ok.text
    s = _summary(c, anm, case_id)
    prose = " ".join(n["text"] for n in s["narrative"])
    assert "Fever for 5 days (as reported)." in prose and "2 days" not in prose


def test_two_runs_that_give_one_symptom_different_durations_are_a_conflict_with_no_value_chosen(case):
    c, anm, case_id = case
    assert extract(c, anm, case_id, text="Fever since 2 days.").status_code == 201
    assert extract(c, anm, case_id, text="Fever since 5 days.").status_code == 201
    s = _summary(c, anm, case_id)
    conflict = [x for x in s["conflicts"] if x["key"] == "duration:fever"]
    assert len(conflict) == 1 and {v["display"] for v in conflict[0]["values"]} == {"2 days", "5 days"}
    prose = " ".join(n["text"] for n in s["narrative"])
    assert "2 days" not in prose and "5 days" not in prose


def test_per_symptom_duration_conflicts_are_pure():
    def fact(fid, key, text):
        return es.Fact(fact_id=fid, section="timeline", key=key, label="Duration", display=text, value={"text": text, "parsed": es.parse_duration(text)},
                       provenance="ai_accepted", trust="checked", sources=[{"type": "test"}])
    assert es.duration_conflicts([fact("A", "duration:fever", "2 days"), fact("B", "duration:headache", "2 hours")]) == []
    assert [c["key"] for c in es.duration_conflicts([fact("A", "duration:fever", "2 days"), fact("B", "duration:fever", "5 days")])] == ["duration:fever"]


def test_the_model_provider_is_untouched_by_the_fake(case):
    c, _, _ = case
    assert isinstance(c.app.state.ai_provider, FakeProvider)
