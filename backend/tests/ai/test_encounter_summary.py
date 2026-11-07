"""Clinical encounter summary (docs/19). Synthetic data only; OCR engines replayed, voice cloud mocked, AI = fake provider.

Covers: duration parsing, the grounding gate (adversarial), the provenance registry, reconciliation, the end-to-end case
(voice + typed text + lab report + handwritten prescription + triage), staleness, review rules, consent, roles, partial
failure, and the safety invariant that the summary never writes triage or review rows."""

import json

import pytest

from app.ai import encounter_summary as es
from app.ai.fake_provider import FakeProvider
from tests.ai.helpers import count, db, extract
from tests.ocr.test_api import FakeEngines, _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import auth, grant, new_case, token_for, triage, withdraw
from tests.voice.test_api import _post as post_audio
from tests.voice.test_api import voice_client  # noqa: F401  (fixture)

URL = "/api/v1/cases/{}/encounter-summary"


# ── pure: duration parsing ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,amount,unit,approx", [
    ("3 days", 3, "day", False), ("three days", 3, "day", False), ("since 2 weeks", 2, "week", False), ("x5d", 5, "day", False),
    ("about 4 hours", 4, "hour", True), ("for the last two months", 2, "month", False), ("a week", 1, "week", False), ("1 day", 1, "day", False),
])
def test_parse_duration(text, amount, unit, approx):
    d = es.parse_duration(text)
    assert d is not None and (d["amount"], d["unit"], d["approximate"]) == (amount, unit, approx) and d["text"] == text


@pytest.mark.parametrize("text", ["", None, "a while", "since yesterday", "recently"])
def test_parse_duration_never_guesses(text):
    assert es.parse_duration(text) is None


def test_duration_display():
    assert es.duration_display(es.parse_duration("three days")) == "3 days"
    assert es.duration_display(es.parse_duration("about 1 day")) == "about 1 day"


# ── pure: provenance registry is exhaustive (the R9 KeyError class) ────────────────────────────────────────


def test_every_provenance_has_label_trust_tag_and_narrative_rule():
    for name, p in es.PROVENANCE.items():
        assert set(p) == {"label", "trust", "tag", "narrative"}, name
        assert p["trust"] in es.TRUST_LEGEND and p["label"]
        assert (p["trust"] == "unchecked") == bool(p["tag"]), f"{name}: unchecked values must carry a tag, checked ones none"
    assert es.PROVENANCE["document_unchecked"]["narrative"] is False  # machine-read document values never reach prose


def _fact(fid, key, display, prov, section="vitals", value=None, label=None):
    return es.Fact(fact_id=fid, section=section, key=key, label=label or key, display=display, value=value if value is not None else display,
                   provenance=prov, trust=es.PROVENANCE[prov]["trust"], sources=[{"type": "test"}])


# ── pure: the grounding gate (adversarial) ────────────────────────────────────────────────────────────────


def test_gate_blocks_unknown_fact_wrong_number_missing_tag_unchecked_document_and_diagnosis():
    facts = {f.fact_id: f for f in (
        _fact("F1", "spo2", "97%", "triage_form", label="SpO2"),
        _fact("F2", "pulse", "96 /min", "ai_unchecked", label="Pulse"),
        _fact("F3", "documented:amox", "Amoxicillin 500 mg", "document_unchecked", section="medications"),
        _fact("F4", "symptom:fever:present", "fever", "ai_accepted", section="symptoms"),
    )}
    sentences = [
        {"section": "vitals", "fact_ids": ["F1"], "text": "SpO2 97%."},  # ok
        {"section": "vitals", "fact_ids": ["F9"], "text": "SpO2 97%."},  # unknown fact
        {"section": "vitals", "fact_ids": ["F1"], "text": "SpO2 79%."},  # number not in the cited fact (transposed digits)
        {"section": "vitals", "fact_ids": ["F2"], "text": "Pulse 96 /min."},  # unchecked without its tag
        {"section": "vitals", "fact_ids": ["F2"], "text": "Pulse 96 /min [not yet checked]."},  # ok
        {"section": "medications", "fact_ids": ["F3"], "text": "Amoxicillin 500 mg [machine-read, not yet checked]."},  # never in prose
        {"section": "symptoms", "fact_ids": ["F4"], "text": "Fever, most likely dengue."},  # diagnostic language
        {"section": "symptoms", "fact_ids": [], "text": "Patient is stable."},  # uncited
        {"section": "vitals", "fact_ids": ["F2"], "names_only": True, "text": "Sources disagree, no value chosen: Pulse."},  # names only: ok untagged
    ]
    passed, blocked = es.gate(sentences, facts)
    assert [s.text for s in passed] == ["SpO2 97%.", "Pulse 96 /min [not yet checked].", "Sources disagree, no value chosen: Pulse."]
    assert [b.reason for b in blocked] == ["uncited_or_unknown_fact", "number_not_in_cited_facts", "missing_unchecked_tag",
                                           "unchecked_document_value_in_prose", "diagnostic_language", "uncited_or_unknown_fact"]


def test_reconcile_merges_equal_values_and_never_chooses_between_different_ones():
    same = [_fact("A", "spo2", "97%", "triage_form", value=97), _fact("B", "spo2", "97%", "voice_confirmed", value=97)]
    rows, conflicts = es.reconcile_rows(same, ("spo2",))
    assert rows[0].display == "97%" and not rows[0].conflict and conflicts == []
    diff = [_fact("A", "temp_c", "37 °C", "triage_form", value=37.0), _fact("B", "temp_c", "38.9 °C", "voice_confirmed", value=38.9)]
    rows, conflicts = es.reconcile_rows(diff, ("temp_c",))
    assert rows[0].display is None and rows[0].conflict and {v["display"] for v in conflicts[0]["values"]} == {"37 °C", "38.9 °C"}
    assert "No value is chosen" in conflicts[0]["action"]


def test_labs_on_different_dates_are_history_not_conflict_and_same_date_differences_conflict():
    v = lambda val, date: {"value": val, "qualitative": None, "unit": "g/dL", "date": date}  # noqa: E731
    rows, conflicts = es.lab_rows([_fact("A", "lab:hb", "11.2 g/dL", "document_confirmed", "labs", v("11.2", "01/09/2026")),
                                   _fact("B", "lab:hb", "9.8 g/dL", "document_confirmed", "labs", v("9.8", "15/09/2026"))])
    assert len(rows) == 2 and conflicts == []
    rows, conflicts = es.lab_rows([_fact("A", "lab:hb", "11.2 g/dL", "document_confirmed", "labs", v("11.2", "15/09/2026")),
                                   _fact("B", "lab:hb", "1.12 g/dL", "document_corrected", "labs", v("1.12", "15/09/2026"))])
    assert len(rows) == 1 and rows[0].conflict and len(conflicts) == 1


# ── API: helpers ──────────────────────────────────────────────────────────────────────────────────────────


def _generate(client, token, case_id):
    r = client.post(URL.format(case_id), headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def _facts_by(summary, section=None, prefix=""):
    return [f for f in summary["facts"].values() if (section is None or f["section"] == section) and f["key"].startswith(prefix)]


def _assert_grounded(summary):
    """Every narrative sentence cites existing facts, its numbers come from them, and every fact has a source."""
    facts = summary["facts"]
    for s in summary["narrative"]:
        assert s["fact_ids"] and all(i in facts for i in s["fact_ids"]), s
        cited = " ".join(f"{facts[i]['label']} {facts[i]['display']}" for i in s["fact_ids"])
        assert es.numbers_in(s["text"]) <= es.numbers_in(cited), s
        assert all(facts[i]["provenance"] != "document_unchecked" for i in s["fact_ids"]), s
    for f in facts.values():
        assert f["sources"], f
    for row in summary["vitals"] + summary["labs"]:
        assert all(i in facts for i in row["fact_ids"])


# ── API: end to end (voice + typed text + lab report + handwritten prescription + triage) ─────────────────


def test_end_to_end_case_produces_a_grounded_source_linked_summary(voice_client, ocr_client):  # noqa: F811
    c = ocr_client  # the same app: both fixtures configure one client
    anm = token_for(c, "anm")
    c.app.state.ai_provider = FakeProvider()
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id, ai=True, voice_cloud=True).status_code == 200

    # voice: "I have had fever for three days. My temperature is 102 degrees Fahrenheit. I do not have chest pain."
    t = post_audio(c, anm, case_id).json()
    cands = {x["field"]: x for x in t["candidates"]}
    for field in ("temp", "symptom_duration"):
        r = c.post(f"/api/v1/cases/{case_id}/voice/candidates/{cands[field]['candidate_id']}/readback", json={"outcome": "confirmed"}, headers=auth(anm))
        assert r.status_code == 200, r.text

    # lab report: Hb confirmed, RBC corrected by the reviewer; platelets left machine-read
    lab = _upload(c, anm, case_id).json()
    assert _attest(c, anm, case_id, lab["document_id"]).status_code == 200
    hb = next(f for f in lab["fields"] if f["analyte_key"] == "hemoglobin")
    rbc = next(f for f in lab["fields"] if f["analyte_key"] == "rbc")
    assert _review(c, anm, case_id, hb, "confirmed").status_code == 200
    assert _review(c, anm, case_id, rbc, "corrected", {"result": {"value": "4.20"}}).status_code == 200

    # handwritten prescription: Pantoprazole confirmed; Amoxicillin readings disagree and stay unchecked
    c.app.state.ocr_engines = FakeEngines("rx_handwritten")
    rx = _upload(c, anm, case_id, "rx_handwritten.png", "prescription").json()
    assert _attest(c, anm, case_id, rx["document_id"]).status_code == 200
    meds = {f["drug_raw"]: f for f in rx["fields"] if f["kind"] == "medication"}
    assert _review(c, anm, case_id, meds["Pantoprazole"], "confirmed").status_code == 200

    # typed intake through the AI extraction (fake provider), then triage with the form's vitals
    assert extract(c, anm, case_id, text="Fever for 3 days with headache. Severe abdominal pain. No chest pain.").status_code == 201
    t_run = triage(c, anm, case_id, vitals={"spo2": 97, "pulse": 96, "sbp": 118, "dbp": 76, "temp_c": 38.9, "resp_rate": 18})
    assert t_run.status_code == 200, t_run.text
    runs_before, reviews_before = count("triage_runs", case_id), count("review_events", case_id)

    v = _generate(c, anm, case_id)
    s = v["summary"]
    assert v["is_stale"] is False and v["review"]["status"] == "draft_pending_review"
    assert s["schema_version"] == es.SUMMARY_SCHEMA_VERSION and s["clinical_use_allowed"] is False and s["requires_sign_off"] is True
    _assert_grounded(s)

    # duration: spoken (confirmed) and typed (AI) agree on 3 days → one value, two sources; never an onset date
    durs = _facts_by(s, "timeline", "duration")
    assert {d["display"] for d in durs} == {"3 days"} and not any(x["key"] == "duration" for x in s["conflicts"])
    assert {d["provenance"] for d in durs} >= {"voice_confirmed"}
    assert not any("onset" in json.dumps(d["value"]) for d in durs)
    duration_sentences = [x for x in s["narrative"] if x["section"] == "timeline"]
    assert len(duration_sentences) == 1 and "3 days" in duration_sentences[0]["text"] and "[not yet checked]" not in duration_sentences[0]["text"]

    # temperature: voice 102 °F (38.9 °C) and triage form 38.9 °C agree → merged, not a conflict
    temp = next(r for r in s["vitals"] if r["key"] == "temp_c")
    assert temp["display"] == "38.9 °C" and not temp["conflict"] and len(temp["fact_ids"]) >= 2
    assert {s["facts"][i]["provenance"] for i in temp["fact_ids"]} >= {"triage_form", "voice_confirmed"}
    assert next(r for r in s["vitals"] if r["key"] == "bp")["display"] == "118/76 mmHg"

    # symptoms: reported and denied kept apart; chest pain denied (spoken + typed)
    present = {s["facts"][i]["display"].lower() for i in s["symptoms"]["present"]}
    assert "fever" in present

    # labs: values with units, document dates, document sources (page image + regions)
    labs = {r["key"].split("@")[0]: r for r in s["labs"]}
    hb_fact = s["facts"][labs["lab:hemoglobin"]["fact_ids"][0]]
    assert hb_fact["provenance"] == "document_confirmed" and "11.2 g/dL" in hb_fact["display"] and hb_fact["value"]["date"] == "15/09/2026"
    assert hb_fact["sources"][0]["document_id"] == lab["document_id"] and hb_fact["sources"][0]["regions"]
    assert s["facts"][labs["lab:rbc"]["fact_ids"][0]]["provenance"] == "document_corrected"
    plt = s["facts"][labs["lab:platelets"]["fact_ids"][0]]
    assert plt["provenance"] == "document_unchecked" and plt["trust"] == "unchecked"

    # medications: reviewed line keeps strength/pattern; uncertain handwriting is shown, flagged, and kept out of prose
    docmeds = {f["value"]["drug_raw"]: f for f in _facts_by(s, "medications", "documented:")}
    assert docmeds["Pantoprazole"]["provenance"] == "document_confirmed" and "40 mg" in docmeds["Pantoprazole"]["display"]
    amox = docmeds["Amoxicillin"]
    assert amox["provenance"] == "document_unchecked" and "uncertain_handwriting" in amox["flags"]
    prose = " ".join(x["text"] for x in s["narrative"])
    assert "Amoxicillin" not in prose and "Pantoprazole 40 mg" in prose and "platelet" not in prose.lower()
    assert any(p["ref"] == amox["sources"][0]["field_id"] for p in s["pending_review"])

    # urgency: copied from the rules engine; the summary wrote no triage or review rows
    assert s["urgency"]["status"] == "rules_engine" and s["urgency"]["recorded_urgency"] == t_run.json()["result"]["urgency"]
    assert s["urgency"]["triage_run_id"] == t_run.json()["run_id"]
    assert [r["rule_id"] for r in s["urgency"]["triggered_rules"]] == [r["rule_id"] for r in t_run.json()["result"]["triggered_rules"]]
    assert count("triage_runs", case_id) == runs_before and count("review_events", case_id) == reviews_before

    # missing info and attention block: pending items are surfaced first, never hidden
    kinds = {a["kind"] for a in s["attention"]}
    assert "pending_review" in kinds and s["status"] == "complete" and s["unavailable_sources"] == []

    # audit: counts and enums only
    with db() as conn:
        row = conn.execute("SELECT details_json FROM audit_log WHERE action = 'encounter_summary_generated' AND case_id = ?", (case_id,)).fetchone()
    details = json.loads(row["details_json"])
    assert details["facts"] == len(s["facts"]) and "Pantoprazole" not in row["details_json"] and "11.2" not in row["details_json"]


# ── API: staleness, reuse, review rules ────────────────────────────────────────────────────────────────────


@pytest.fixture
def ai_case(ai_client):
    anm = token_for(ai_client, "anm")
    case_id = new_case(ai_client, anm)
    assert grant(ai_client, anm, case_id, ai=True).status_code == 200
    return ai_client, anm, case_id


def test_same_sources_reuse_the_summary_and_any_change_marks_it_stale(ai_case):
    c, anm, case_id = ai_case
    ext = extract(c, anm, case_id, text="Fever for 3 days. SpO2 91%.").json()
    first = _generate(c, anm, case_id)
    assert _generate(c, anm, case_id)["summary_id"] == first["summary_id"]  # nothing changed: nothing new stored
    assert count("encounter_summaries", case_id) == 1

    f = next(x for x in ext["fields"] if x["field"] == "spo2")
    assert c.post(f"/api/v1/cases/{case_id}/ai/fields/{f['field_id']}/review", json={"outcome": "accepted"}, headers=auth(anm)).status_code == 200
    got = c.get(URL.format(case_id), headers=auth(anm)).json()
    assert got["summary_id"] == first["summary_id"] and got["is_stale"] is True and got["stale_message"]

    r = c.post(f"{URL.format(case_id)}/{first['summary_id']}/review", json={"outcome": "accepted", "acknowledged_conflicts": True}, headers=auth(anm))
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUMMARY_STALE"
    second = _generate(c, anm, case_id)
    assert second["summary_id"] != first["summary_id"] and second["is_stale"] is False
    spo2 = next(r for r in second["summary"]["vitals"] if r["key"] == "spo2")
    assert second["summary"]["facts"][spo2["fact_ids"][0]]["provenance"] == "ai_accepted"
    r = c.post(f"{URL.format(case_id)}/{first['summary_id']}/review", json={"outcome": "accepted"}, headers=auth(anm))
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUMMARY_SUPERSEDED"
    assert [x["summary_id"] for x in c.get(f"{URL.format(case_id)}/history", headers=auth(anm)).json()["summaries"]] == [first["summary_id"], second["summary_id"]]


def test_conflicts_must_be_acknowledged_and_reviews_are_append_only_with_supersedes(ai_case):
    c, anm, case_id = ai_case
    extract(c, anm, case_id, text="Fever for 3 days. SpO2 91%.")
    assert triage(c, anm, case_id, vitals={"spo2": 97}).status_code == 200  # form says 97, intake says 91 → conflict
    v = _generate(c, anm, case_id)
    s = v["summary"]
    spo2 = next(r for r in s["vitals"] if r["key"] == "spo2")
    assert spo2["conflict"] and spo2["display"] is None and any(a["kind"] == "conflict" for a in s["attention"])
    assert "Sources disagree, no value chosen: SpO2" in " ".join(x["text"] for x in s["narrative"])
    assert s["attention"][0]["kind"] in ("conflict", "unavailable_source")  # conflicts lead the page

    url = f"{URL.format(case_id)}/{v['summary_id']}/review"
    r = c.post(url, json={"outcome": "accepted"}, headers=auth(anm))
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONFLICTS_NOT_ACKNOWLEDGED"
    runs, reviews = count("triage_runs", case_id), count("review_events", case_id)
    first = c.post(url, json={"outcome": "needs_changes"}, headers=auth(anm))
    assert first.status_code == 200 and first.json()["review"]["status"] == "needs_changes"
    eid = first.json()["review"]["latest"]["event_id"]
    stale = c.post(url, json={"outcome": "accepted", "acknowledged_conflicts": True}, headers=auth(anm))  # does not name the event it supersedes
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVIEW_CONFLICT"
    ok = c.post(url, json={"outcome": "accepted", "acknowledged_conflicts": True, "supersedes": eid}, headers=auth(anm))
    assert ok.status_code == 200 and ok.json()["review"]["status"] == "accepted" and len(ok.json()["review"]["history"]) == 2
    assert count("triage_runs", case_id) == runs and count("review_events", case_id) == reviews  # triage untouched
    assert c.post(url, json={"outcome": "accepted", "note": "free text"}, headers=auth(anm)).status_code in (400, 422)  # no free text accepted


def test_no_triage_run_means_urgency_not_determined_and_missing_vitals_are_listed_not_normal(ai_case):
    c, anm, case_id = ai_case
    extract(c, anm, case_id, text="Headache since yesterday.")
    s = _generate(c, anm, case_id)["summary"]
    assert s["urgency"] == {"status": "not_determined", "recorded_urgency": None, "message": s["urgency"]["message"]}
    assert any(a["kind"] == "no_triage_run" for a in s["attention"])
    missing = {m["key"] for m in s["missing_information"]}
    assert {"red_flag_screen", "spo2", "bp", "pulse"} <= missing and "duration" in missing
    assert "normal" not in json.dumps(s["missing_information"]).lower().replace("never treated as normal", "")
    assert s["follow_up_questions"] and s["follow_up_questions"][0]["field_name"] == "red_flag_screen"


def test_linked_duration_sentence_reads_as_a_sentence(ai_case):
    c, anm, case_id = ai_case
    extract(c, anm, case_id, text="Fever for 3 days.")
    timeline = [x["text"] for x in _generate(c, anm, case_id)["summary"]["narrative"] if x["section"] == "timeline"]
    assert timeline == ["Fever for 3 days [not yet checked] (as reported)."]


def test_contradictory_durations_stay_visible_and_neither_is_narrated(ai_case):
    c, anm, case_id = ai_case
    extract(c, anm, case_id, text="Fever for 3 days.")
    extract(c, anm, case_id, text="Fever for 2 weeks.")  # a second run: every run is read, not only the latest
    s = _generate(c, anm, case_id)["summary"]
    conflict = next(x for x in s["conflicts"] if x["key"] == "duration")
    assert {v["display"] for v in conflict["values"]} == {"3 days", "2 weeks"}
    assert all(x["text"][:1].isupper() for x in s["narrative"])  # every sentence starts with a capital
    prose = " ".join(x["text"] for x in s["narrative"])
    assert "3 days" not in prose and "2 weeks" not in prose and "Different durations were recorded" in prose


def test_rejected_ai_fields_are_dropped_and_corrected_ones_are_labelled(ai_case):
    c, anm, case_id = ai_case
    ext = extract(c, anm, case_id).json()  # INTAKE: fever 3 days, BP 150/90, SpO2 91%, no chest pain, paracetamol
    fields = {f["field"]: f for f in ext["fields"]}
    review = lambda f, body: c.post(f"/api/v1/cases/{case_id}/ai/fields/{f['field_id']}/review", json=body, headers=auth(anm))  # noqa: E731
    assert review(fields["spo2"], {"outcome": "rejected"}).status_code == 200
    assert review(fields["bp"], {"outcome": "corrected", "corrected": {"value": 140, "value2": 90, "unit": "mmHg"}}).status_code == 200
    s = _generate(c, anm, case_id)["summary"]
    assert not any(r["key"] == "spo2" for r in s["vitals"])
    bp = next(r for r in s["vitals"] if r["key"] == "bp")
    assert bp["display"] == "140/90 mmHg" and s["facts"][bp["fact_ids"][0]]["provenance"] == "ai_corrected"
    prose = " ".join(x["text"] for x in s["narrative"])
    assert "Blood pressure 140/90 mmHg" in prose and "150/90" not in prose and "Ramesh" not in json.dumps(s)


# ── API: consent, roles, partial failure ──────────────────────────────────────────────────────────────────


def test_without_ai_assist_the_summary_excludes_ai_fields_and_says_so(ai_client):
    anm = token_for(ai_client, "anm")
    case_id = new_case(ai_client, anm)
    grant(ai_client, anm, case_id)  # triage only
    assert triage(ai_client, anm, case_id).status_code == 200
    s = _generate(ai_client, anm, case_id)["summary"]
    assert s["status"] == "complete" and s["excluded_sources"] == [{"source": "ai_extraction", "reason": "ai_assist_consent_not_in_effect"}]
    assert next(r for r in s["vitals"] if r["key"] == "spo2")["display"]


def test_withdrawn_consent_and_wrong_roles_are_refused(ai_case):
    c, anm, case_id = ai_case
    _generate(c, anm, case_id)
    assert c.get(URL.format(case_id), headers=auth(token_for(c, "patient"))).status_code == 403
    assert c.get(URL.format(case_id), headers=auth(token_for(c, "supervisor"))).status_code == 403
    assert c.get(URL.format(case_id), headers=auth(token_for(c, "anm_other"))).status_code == 404  # not their case: looks missing
    assert c.get(URL.format(case_id), headers=auth(token_for(c, "mo"))).status_code == 200
    withdraw(c, anm, case_id, "triage")
    assert c.get(URL.format(case_id), headers=auth(anm)).status_code == 403
    assert c.post(URL.format(case_id), headers=auth(anm)).status_code == 403


def test_a_failing_source_makes_the_summary_partial_visible_and_unacceptable(ai_case, monkeypatch):
    c, anm, case_id = ai_case
    assert triage(c, anm, case_id).status_code == 200

    async def boom(*a, **k):
        raise RuntimeError("synthetic failure with a value 39.5 that must not leak")

    monkeypatch.setattr(es, "SOURCES", tuple((n, boom if n == "body_map" else fn) for n, fn in es.SOURCES))
    v = _generate(c, anm, case_id)
    s = v["summary"]
    assert s["status"] == "partial" and s["unavailable_sources"] == [{"source": "body_map", "reason": "read_failed"}]
    assert s["attention"][0]["kind"] == "unavailable_source"
    assert s["urgency"]["recorded_urgency"]  # the rest of the case is still there
    r = c.post(f"{URL.format(case_id)}/{v['summary_id']}/review", json={"outcome": "accepted"}, headers=auth(anm))
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUMMARY_PARTIAL"
    assert "39.5" not in json.dumps(s)


def test_summary_module_never_computes_or_writes_triage():
    from pathlib import Path

    src = Path(es.__file__).read_text()
    for forbidden in ("evaluate_triage", "INSERT INTO triage_runs", "INSERT INTO review_events", "run_case_triage", "enforce_raise_only"):
        assert forbidden not in src, forbidden


def test_summary_tables_are_append_only(ai_case):
    import sqlite3

    c, anm, case_id = ai_case
    v = _generate(c, anm, case_id)
    c.post(f"{URL.format(case_id)}/{v['summary_id']}/review", json={"outcome": "needs_changes"}, headers=auth(anm))
    with db() as conn:
        for sql in ("UPDATE encounter_summaries SET status = 'complete'", "DELETE FROM encounter_summaries",
                    "UPDATE encounter_summary_review_events SET outcome = 'accepted'", "DELETE FROM encounter_summary_review_events"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def test_withdrawing_ai_assist_withholds_a_stored_summary_with_ai_content(ai_case):
    c, anm, case_id = ai_case
    extract(c, anm, case_id, text="Fever for 3 days.")
    assert "3 days" in json.dumps(_generate(c, anm, case_id)["summary"])
    withdraw(c, anm, case_id, "ai_assist")
    got = c.get(URL.format(case_id), headers=auth(anm)).json()
    assert got["summary"] is None and got["is_stale"] is True and "3 days" not in json.dumps(got)
    fresh = _generate(c, anm, case_id)["summary"]  # rebuilt without AI fields, and says so
    assert fresh["excluded_sources"] == [{"source": "ai_extraction", "reason": "ai_assist_consent_not_in_effect"}] and "3 days" not in json.dumps(fresh)
