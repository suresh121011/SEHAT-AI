"""P0-2 "Check these first" verification queue (docs/implementation/p0-2-verification.md). Read-only: it orders what a
person should check, and never confirms, changes or records anything. Synthetic data only; engines replayed."""

import sqlite3

from app import verification_queue as vq
from app.config import get_settings
from app.rules import TriageInput, evaluate_triage
from tests.ocr.test_api import _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.ocr.test_lab_flag_end_to_end import KftEngines
from tests.privacy.helpers import auth, grant, new_case, token_for, triage, triage_case
from tests.voice.test_api import _post as post_audio
from tests.voice.test_api import voice_client  # noqa: F401  (fixture)

URL = "/api/v1/cases/{}/verification-queue"


def _doc(rows, attestation="matches", status="completed"):
    return {"document_id": "d1", "document_type": "lab_report", "status": status,
            "attestation": None if attestation is None else {"answer": attestation}, "fields": rows}


def _lab(fid, name, flag=None, status="machine_read", range_status="within_range", band="amber", disputed=False):
    return {"field_id": fid, "kind": "lab", "name_raw": name, "analyte_key": name.lower(), "value": {"raw": "1"}, "unit": {"raw": ""},
            "printed_flag": flag, "printed_range_status": range_status, "review_status": status, "band": band, "disputed": disputed,
            "can_confirm": not disputed, "reference": None}


# ── pure ranking ────────────────────────────────────────────────────────────────────────────────────────────

def test_abnormal_labs_come_before_routine_rows_and_checked_rows_are_left_out():
    rows = [_lab("a", "Sodium"), _lab("b", "Urea", flag="L", range_status="below_range"), _lab("c", "Calcium", status="confirmed"),
            _lab("d", "Potassium", range_status="above_range")]
    items = vq.rank([_doc(rows)], [], None)
    assert [i["ref"].get("field_id") for i in items] == ["b", "d", "a"]
    assert [i["priority"] for i in items] == ["abnormal_lab", "abnormal_lab", "routine"]
    assert "printed_flag_low" in items[0]["reasons"] and "outside_printed_range" in items[1]["reasons"]


def test_an_unmatched_document_is_first_and_its_rows_say_they_are_blocked():
    items = vq.rank([_doc([_lab("b", "Urea", flag="L")], attestation=None)], [], None)
    assert items[0]["kind"] == "document_attestation" and items[0]["priority"] == "match_document"
    assert items[1]["ref"]["field_id"] == "b" and "document_not_matched_to_patient" in items[1]["reasons"]


def test_unsure_rows_stay_in_the_queue_and_deleted_documents_are_ignored():
    items = vq.rank([_doc([_lab("u", "Urea", status="unsure")]), _doc([_lab("x", "Hb")], status="deleted")], [], None)
    assert [i["ref"]["field_id"] for i in items] == ["u"] and "marked_not_sure" in items[0]["reasons"]


def test_spoken_vital_that_would_change_the_rules_urgency_is_first():
    base = TriageInput.model_validate(triage_case(vitals={"spo2": 97, "pulse": 80, "sbp": 120, "dbp": 80, "temp_c": 37.0, "resp_rate": 16}))
    low = {"candidate_id": "v1", "field": "spo2", "normalized": {"spo2": 85}, "display": "85 %", "state": "undecided", "can_confirm": True}
    same = {"candidate_id": "v2", "field": "pulse", "normalized": {"pulse": 82}, "display": "82 bpm", "state": "undecided", "can_confirm": True}
    items = vq.rank([_doc([_lab("b", "Urea", flag="L")])], [same, low], base)
    first = items[0]
    expected = evaluate_triage(base.model_copy(update={"vitals": base.vitals.model_copy(update={"spo2": 85})})).urgency.value
    assert first["ref"]["candidate_id"] == "v1" and first["priority"] == "could_change_urgency"
    assert first["urgency_if_confirmed"] == {"now": evaluate_triage(base).urgency.value, "if_confirmed": expected, "hypothetical": True}
    assert next(i for i in items if i["ref"].get("candidate_id") == "v2")["priority"] == "routine"


def test_without_a_triage_run_no_urgency_change_is_claimed():
    v = {"candidate_id": "v1", "field": "spo2", "normalized": {"spo2": 85}, "display": "85 %", "state": "undecided", "can_confirm": True}
    [item] = vq.rank([], [v], None)
    assert item["priority"] == "routine" and "no_triage_run_to_compare" in item["reasons"] and item["urgency_if_confirmed"] is None


def test_unreadable_spoken_value_is_queued_without_a_hypothetical():
    v = {"candidate_id": "v1", "field": "temp", "normalized": None, "display": "202", "state": "undecided", "can_confirm": False}
    base = TriageInput.model_validate(triage_case())
    [item] = vq.rank([], [v], base)
    assert item["priority"] == "routine" and "needs_correction" in item["reasons"] and item["urgency_if_confirmed"] is None


# ── API ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_queue_orders_a_real_upload_and_shrinks_as_rows_are_checked(ocr_client):  # noqa: F811
    c = ocr_client
    c.app.state.ocr_engines = KftEngines()
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id).status_code == 200
    doc = _upload(c, anm, case_id).json()

    q = c.get(URL.format(case_id), headers=auth(anm)).json()
    kinds = [(i["kind"], i["priority"], i["label"]) for i in q["items"]]
    assert kinds[0][:2] == ("document_attestation", "match_document")
    assert [k[2] for k in kinds[1:3]] == ["SERUM UREA", "SERUM CREATININE"]  # printed L flags
    assert kinds[3][1] == "routine" and q["counts"]["total"] == 4
    assert q["clinical_use_allowed"] is False and "never" in q["note"].lower()

    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    urea = next(f for f in doc["fields"] if f["analyte_key"] == "urea")
    assert _review(c, anm, case_id, urea, "confirmed").status_code == 200
    q2 = c.get(URL.format(case_id), headers=auth(anm)).json()
    assert [i["label"] for i in q2["items"]] == ["SERUM CREATININE", "SERUM CALCIUM"]


def test_queue_writes_nothing_and_refuses_without_access(ocr_client):  # noqa: F811
    c = ocr_client
    c.app.state.ocr_engines = KftEngines()
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id).status_code == 200
    _upload(c, anm, case_id)
    with sqlite3.connect(get_settings().database_path) as db:
        before = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ocr_review_events", "ocr_attestation_events", "triage_runs", "audit_log")}
    assert c.get(URL.format(case_id), headers=auth(anm)).status_code == 200
    with sqlite3.connect(get_settings().database_path) as db:
        after = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    assert after == before
    assert c.get(URL.format(case_id), headers=auth(token_for(c, "patient"))).status_code == 403
    other = new_case(c, anm)
    assert c.get(URL.format(other), headers=auth(anm)).status_code in (403, 409)  # no triage consent yet


def test_spoken_low_spo2_is_ranked_first_through_the_api(voice_client, ocr_client):  # noqa: F811
    c = ocr_client
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id, voice_cloud=True).status_code == 200
    vitals = {"spo2": 97, "pulse": 80, "sbp": 120, "dbp": 80, "temp_c": 37.0, "resp_rate": 16}
    assert triage(c, anm, case_id, vitals=vitals).status_code == 200
    base = TriageInput.model_validate(triage_case(vitals=vitals))
    now = evaluate_triage(base).urgency.value
    if_85 = evaluate_triage(base.model_copy(update={"vitals": base.vitals.model_copy(update={"spo2": 85})})).urgency.value
    assert now != if_85  # precondition of this scenario, decided by the rules engine itself
    c.fake.transcript = "oxygen 85 percent"
    assert post_audio(c, anm, case_id).status_code == 200
    first = c.get(URL.format(case_id), headers=auth(anm)).json()["items"][0]
    assert (first["kind"], first["field"], first["priority"], first["act_on"]) == ("voice_value", "spo2", "could_change_urgency", "voice")
    assert first["urgency_if_confirmed"] == {"now": now, "if_confirmed": if_85, "hypothetical": True}


# ── P0-2 review: flag order, agreement is not verification, roles ───────────────────────────────────────────

def _cand(flag: str):
    from types import SimpleNamespace

    return SimpleNamespace(flag_raw=flag)


def test_flag_before_or_after_the_value_compares_as_the_same_value():
    from app.ocr.verify import _cell_value_text, _same_value

    for flag in ("L", "H"):
        for cell in (f"{flag} 17", f"17 {flag}"):
            assert _same_value(_cell_value_text(cell, _cand(flag)), "17"), (flag, cell)
    assert not _same_value(_cell_value_text("L 18", _cand("L")), "17")  # a different number stays a disagreement
    assert not _same_value(_cell_value_text("L 17", _cand("")), "17")  # a flag the first reading missed stays visible
    assert not _same_value(_cell_value_text("H 17", _cand("L")), "17")  # a different flag is not removed


def test_engines_agreeing_is_not_a_human_check(ocr_client):  # noqa: F811
    c = ocr_client
    c.app.state.ocr_engines = KftEngines()
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id).status_code == 200
    doc = _upload(c, anm, case_id).json()
    calcium = next(f for f in doc["fields"] if f["analyte_key"] == "calcium")
    assert ("second_engine_agreement", "pass") in {(ch["check"], ch["status"]) for ch in calcium["checks"]}
    assert calcium["review_status"] == "machine_read"
    q = c.get(URL.format(case_id), headers=auth(anm)).json()
    assert any(i["ref"].get("field_id") == calcium["field_id"] for i in q["items"])  # still to be checked by a person


def test_supervisor_cannot_read_the_queue(ocr_client):  # noqa: F811
    c = ocr_client
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id).status_code == 200
    assert c.get(URL.format(case_id), headers=auth(token_for(c, "supervisor"))).status_code == 403
