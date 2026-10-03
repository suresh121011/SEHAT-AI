"""Phase 6 P1: missing information, follow-up questions, counterfactuals (docs/16). Synthetic data only."""

import sqlite3

import pytest

from app.ai import question_bank
from app.ai.fake_provider import FakeProvider
from app.rules import TriageInput, evaluate_triage
from app.rules.counterfactual import counterfactuals
from app.rules.required_fields import missing
from tests.ai.helpers import by_field, extract
from tests.privacy.helpers import auth, grant, new_case, token_for, triage
from tests.rules.vignettes import case as vignette

# ── Missing information and questions (pure) ─────────────────────────────────────────────────────


def test_red_flag_screen_is_always_asked_first_and_never_inferred():
    gaps = missing("opd", {"chief_complaint", "duration", "pain_severity", "spo2", "bp", "pulse"})
    assert [g.key for g in gaps] == ["red_flag_screen"]


def test_opd_missing_items_follow_the_architecture_list():
    keys = [g.key for g in missing("opd", {"chief_complaint", "bp"})]
    assert keys[0] == "red_flag_screen" and set(keys) == {"red_flag_screen", "duration", "severity", "spo2", "pulse"}


def test_maternal_danger_signs_come_first_and_ocr_hb_counts():
    keys = [g.key for g in missing("maternal", {"ocr:hb", "bp"})]
    assert keys[:2] == ["red_flag_screen", "danger_signs"] and "hb" not in keys


def test_ncd_needs_two_bp_readings_and_any_medication():
    keys = {g.key for g in missing("chronic_ncd", {"bp", "medication:metformin"})}
    assert "bp_reading_2" in keys and "bp_reading_1" not in keys and "current_drugs" not in keys


def test_questions_are_capped_english_and_danger_first():
    qs = question_bank.questions_for(missing("maternal", set()))
    assert len(qs) == question_bank.MAX_QUESTIONS and qs[0]["is_danger_sign"] and qs[1]["is_danger_sign"]
    assert all(q["language"] == "en" and q["translations"] == {"hi": "not_available_pending_review", "or": "not_available_pending_review"} for q in qs)


# ── Counterfactuals (pure: the engine is the only judge) ─────────────────────────────────────────


def test_counterfactuals_are_confirmed_by_the_engine():
    data = TriageInput(**vignette(vitals={"spo2": 93}))
    out = counterfactuals(data)
    assert out["counterfactuals"]
    for cf in out["counterfactuals"]:
        assert cf["then_urgency"] != out["urgency"]
        ch = cf["if_changed"]
        d = data.model_dump(mode="json")
        if ch["field"].startswith("vitals."):
            d["vitals"][ch["field"].split(".")[1]] = ch["to"]
        elif ch["field"] == "red_flags_present":
            d["red_flags_present"].remove(ch["remove"])
        else:
            d["red_flag_screen_completed"] = False
            d["red_flags_present"] = []
        assert evaluate_triage(TriageInput.model_validate(d)).urgency.value == cf["then_urgency"]


def test_removing_the_only_red_flag_is_a_counterfactual():
    out = counterfactuals(TriageInput(**vignette(red_flags_present=["chest_pain_acute_24h"])))
    assert out["urgency"] == "RED"
    assert any(c["if_changed"] == {"field": "red_flags_present", "remove": "chest_pain_acute_24h"} for c in out["counterfactuals"])


def test_counterfactuals_limit():
    assert len(counterfactuals(TriageInput(**vignette()), limit=2)["counterfactuals"]) <= 2


# ── API ──────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def anm(ai_client):
    return token_for(ai_client, "anm")


@pytest.fixture
def cid(ai_client, anm):
    c = new_case(ai_client, anm)
    grant(ai_client, anm, c, ai=True)
    ai_client.app.state.ai_provider = FakeProvider()
    return c


def test_extraction_view_lists_missing_information_and_questions(ai_client, anm, cid):
    v = extract(ai_client, anm, cid, text="Fever for 3 days. BP 150/90.").json()
    missing_keys = [m["field_name"] for m in v["missing_information"]]
    assert missing_keys[0] == "red_flag_screen" and "spo2" in missing_keys and "duration" not in missing_keys
    assert all(m["status"] == "needs_human_review" for m in v["missing_information"])
    assert v["follow_up_questions"][0]["field_name"] == "red_flag_screen"


def test_rejected_value_becomes_missing_again(ai_client, anm, cid):
    v = extract(ai_client, anm, cid, text="Fever for 3 days. BP 150/90.").json()
    bp = by_field(v)["bp"]
    ai_client.post(f"/api/v1/cases/{cid}/ai/fields/{bp['field_id']}/review", json={"outcome": "rejected"}, headers=auth(anm))
    again = ai_client.get(f"/api/v1/cases/{cid}/ai/extractions/{v['extraction_id']}", headers=auth(anm)).json()
    assert "bp" in [m["field_name"] for m in again["missing_information"]]


def test_note_includes_counterfactuals_from_the_stored_triage_input(ai_client, anm, cid):
    run = triage(ai_client, anm, cid, vitals={"spo2": 93}).json()
    eid = extract(ai_client, anm, cid).json()["extraction_id"]
    note = ai_client.post(f"/api/v1/cases/{cid}/ai/notes", json={"extraction_id": eid}, headers=auth(anm)).json()
    assert note["counterfactuals"]["status"] == "computed" and note["counterfactuals"]["urgency"] == run["result"]["urgency"]
    per_run = ai_client.get(f"/api/v1/cases/{cid}/triage/runs/{run['run_id']}/counterfactuals", headers=auth(anm)).json()
    assert per_run["status"] == "computed" and per_run["counterfactuals"] == note["counterfactuals"]["counterfactuals"]


def test_runs_without_stored_input_report_unavailable(ai_client, anm, cid):
    from app.config import get_settings

    with sqlite3.connect(get_settings().database_path) as c:
        seq = c.execute("SELECT MAX(seq) FROM consent_events WHERE case_id = ?", (cid,)).fetchone()[0]
        c.execute("INSERT INTO triage_runs (run_id, case_id, consent_seq, urgency, result_json, engine_version, ruleset_version, actor_id, created_at) "
                  "VALUES ('00000000-0000-4000-8000-000000000001', ?, ?, 'GREEN', ?, 'e', 'r', 'a', '2026-10-01T00:00:00')",
                  (cid, seq, evaluate_triage(TriageInput(**vignette())).model_dump_json()))
    r = ai_client.get(f"/api/v1/cases/{cid}/triage/runs/00000000-0000-4000-8000-000000000001/counterfactuals", headers=auth(anm)).json()
    assert r["status"] == "unavailable"


def test_stateless_counterfactuals_endpoint(ai_client, anm):
    r = ai_client.post("/api/v1/triage/counterfactuals", json=vignette(vitals={"spo2": 93}), headers=auth(anm))
    assert r.status_code == 200 and r.json()["counterfactuals"]
    assert ai_client.post("/api/v1/triage/counterfactuals", json=vignette(), headers=auth(token_for(ai_client, "patient"))).status_code == 403


def test_run_counterfactuals_need_triage_consent(ai_client, anm, cid):
    run = triage(ai_client, anm, cid).json()
    ai_client.post(f"/api/v1/cases/{cid}/consent/withdraw", json={"purpose": "triage"}, headers=auth(anm))
    r = ai_client.get(f"/api/v1/cases/{cid}/triage/runs/{run['run_id']}/counterfactuals", headers=auth(anm))
    assert r.status_code == 403
