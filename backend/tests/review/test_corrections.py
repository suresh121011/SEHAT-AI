"""Phase 8 hardening (docs/17 §3a): reviewer corrections as new, run-bound rules-engine runs.

Synthetic data only. The concurrency test opens two real aiosqlite connections to the same file so the two
corrections genuinely contend for SQLite's write lock (a sync TestClient would only ever run them in sequence).
"""

import asyncio
import json
import sqlite3
import uuid

import pytest

from app import review_queue
from app.auth import Principal, Role, demo_user_id
from app.config import get_settings
from app.database import _connect
from app.rules import TriageInput
from tests.privacy.helpers import audit_rows, auth, grant, new_case, token_for, triage, withdraw

API = "/api/v1"
RED = {"vitals": {"resp_rate": 30}}


@pytest.fixture
def anm(client):
    return token_for(client, "anm")


@pytest.fixture
def mo(client):
    return token_for(client, "mo")


@pytest.fixture
def sup(client):
    return token_for(client, "supervisor")


def make_case(client, anm, **inputs) -> tuple[str, str]:
    cid = new_case(client, anm)
    assert grant(client, anm, cid).status_code == 200
    r = triage(client, anm, cid, **inputs)
    assert r.status_code == 200, r.text
    return cid, r.json()["run_id"]


def review(client, tok, cid) -> dict:
    r = client.get(f"{API}/triage/{cid}", headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def with_vitals(inp: dict, **vitals) -> dict:
    return {**inp, "vitals": {**inp["vitals"], **vitals}}


def correct(client, tok, cid, expected, inp, code="remeasured", text=None, **extra):
    body = {"expected_triage_run_id": expected, "input": inp, "reason_code": code, "reason_text": text, "confirm": True, **extra}
    return client.post(f"{API}/triage/{cid}/corrections", json=body, headers=auth(tok))


def db():
    c = sqlite3.connect(get_settings().database_path)
    c.row_factory = sqlite3.Row
    return c


def test_correction_creates_a_new_linked_run_and_keeps_the_old_one(client, anm, mo):
    cid, run1 = make_case(client, anm, **RED)
    inp = review(client, mo, cid)["latest"]["input"]
    with db() as c:
        before = dict(c.execute("SELECT * FROM triage_runs WHERE run_id = ?", (run1,)).fetchone())
    r = correct(client, mo, cid, run1, with_vitals(inp, resp_rate=16), "entry_error")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["corrects_run_id"] == run1 and out["urgency_before"] == "RED" and out["changed_fields"] == ["vitals.resp_rate"]
    with db() as c:
        assert dict(c.execute("SELECT * FROM triage_runs WHERE run_id = ?", (run1,)).fetchone()) == before  # untouched
        new = c.execute("SELECT * FROM triage_runs WHERE run_id = ?", (out["triage_run_id"],)).fetchone()
        assert new["corrects_run_id"] == run1 and new["correction_reason_code"] == "entry_error" and new["actor_id"] == demo_user_id("mo_demo")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE triage_runs SET correction_reason_code = 'other' WHERE run_id = ?", (out["triage_run_id"],))
    actions = [(a["action"], json.loads(a["details_json"])) for a in audit_rows(client) if a["case_id"] == cid]
    corrected = [d for a, d in actions if a == "triage_corrected"]
    assert corrected == [{"from_run_id": run1, "to_run_id": out["triage_run_id"], "rules_urgency_before": "RED", "rules_urgency_after": out["urgency"],
                          "changed_fields": ["vitals.resp_rate"], "reason_code": "entry_error", "has_reason_text": False}]
    assert ("triage_recorded", ) == tuple(a for a, d in actions if d.get("run_id") == out["triage_run_id"])  # same rules path as intake


def test_stale_expected_run_is_refused_and_nothing_is_written(client, anm, mo):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    run2 = triage(client, anm, cid).json()["run_id"]  # someone else recorded a newer run meanwhile
    r = correct(client, mo, cid, run1, with_vitals(inp, spo2=90))
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN"
    assert r.json()["error"]["details"]["latest_triage_run_id"] == run2
    with db() as c:
        assert c.execute("SELECT COUNT(*) FROM triage_runs WHERE case_id = ?", (cid,)).fetchone()[0] == 2


def test_a_run_can_be_corrected_only_once(client, anm, mo):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    assert correct(client, mo, cid, run1, with_vitals(inp, spo2=90)).status_code == 201
    r = correct(client, mo, cid, run1, with_vitals(inp, spo2=91))
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN"
    with db() as c, pytest.raises(sqlite3.IntegrityError):  # the partial unique index, independent of the API check
        row = dict(c.execute("SELECT * FROM triage_runs WHERE run_id = ?", (run1,)).fetchone())
        c.execute("INSERT INTO triage_runs (run_id, case_id, consent_seq, urgency, result_json, engine_version, ruleset_version, actor_id, created_at, corrects_run_id) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), cid, row["consent_seq"], row["urgency"], row["result_json"], row["engine_version"],
                                                          row["ruleset_version"], "x", row["created_at"], run1))


def test_concurrent_corrections_of_the_same_run_commit_exactly_once(client, anm, mo):
    cid, run1 = make_case(client, anm)
    inp = TriageInput.model_validate(review(client, mo, cid)["latest"]["input"])
    principal = Principal(user_id=demo_user_id("mo_demo"), username="mo_demo", role=Role.MEDICAL_OFFICER)
    path = get_settings().database_path

    async def one(spo2: int):
        conn = await _connect(path)
        try:
            body = review_queue.CorrectionBody(expected_triage_run_id=uuid.UUID(run1), input=inp.model_copy(update={"vitals": inp.vitals.model_copy(update={"spo2": spo2})}),
                                               reason_code="remeasured", confirm=True)
            return await review_queue.correct(conn, principal, cid, body, None)
        except Exception as exc:  # noqa: BLE001 — the loser's error is the assertion target
            return exc
        finally:
            await conn.close()

    async def both():
        return await asyncio.gather(one(90), one(91))

    results = asyncio.run(both())
    ok = [r for r in results if isinstance(r, dict)]
    errs = [r for r in results if not isinstance(r, dict)]
    assert len(ok) == 1 and len(errs) == 1, results
    assert getattr(errs[0], "status_code", None) == 409 and errs[0].code == "STALE_TRIAGE_RUN"
    with db() as c:
        assert c.execute("SELECT COUNT(*) FROM triage_runs WHERE case_id = ?", (cid,)).fetchone()[0] == 2


def test_no_change_points_to_sign_off(client, anm, mo):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    r = correct(client, mo, cid, run1, inp)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_CHANGE" and "sign off" in r.json()["error"]["message"]


@pytest.mark.parametrize("mutate, field", [
    (lambda b: b.pop("reason_code"), "reason_code"),
    (lambda b: b.update(reason_code="typo"), "reason_code"),
    (lambda b: b.update(reason_code=3), "reason_code"),
    (lambda b: b.update(reason_code="other", reason_text="short"), ""),
    (lambda b: b.update(reason_text="x" * 301), "reason_text"),
    (lambda b: b.update(confirm=False), "confirm"),
    (lambda b: b.pop("confirm"), "confirm"),
    (lambda b: b.update(expected_triage_run_id="not-a-uuid"), "expected_triage_run_id"),
    (lambda b: b.update(reviewer_id="someone-else"), "<extra>"),
    (lambda b: b["input"]["vitals"].update(spo2=101), "input.vitals.spo2"),
])
def test_server_validation(client, anm, mo, mutate, field):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    body = {"expected_triage_run_id": run1, "input": with_vitals(inp, spo2=90), "reason_code": "remeasured", "reason_text": None, "confirm": True}
    mutate(body)
    r = client.post(f"{API}/triage/{cid}/corrections", json=body, headers=auth(mo))
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert field in [e["field"] for e in r.json()["error"]["details"]["errors"]]


def test_other_with_explanation_and_identifier_check(client, anm, mo):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    r = correct(client, mo, cid, run1, with_vitals(inp, spo2=90), "other", "Call 9876543210 for details")
    assert r.status_code == 422 and r.json()["error"]["code"] == "PII_DETECTED"
    r = correct(client, mo, cid, run1, with_vitals(inp, spo2=90), "other", "Pulse oximeter probe was misplaced on the first reading")
    assert r.status_code == 201, r.text


def test_scenario_cannot_be_changed_by_a_correction(client, anm, mo):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    r = correct(client, mo, cid, run1, {**inp, "scenario": "campus_fever"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SCENARIO_MISMATCH"


def test_only_a_medical_officer_can_correct(client, anm, mo, sup):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    for tok in (anm, sup):
        r = correct(client, tok, cid, run1, with_vitals(inp, spo2=90))
        assert r.status_code == 403
    spoof = client.post(f"{API}/triage/{cid}/corrections", headers={**auth(mo), "X-SEHAT-User-ID": demo_user_id("anm_demo")},
                        json={"expected_triage_run_id": run1, "input": with_vitals(inp, spo2=90), "reason_code": "remeasured", "confirm": True})
    assert spoof.status_code == 403


def test_correction_needs_triage_consent(client, anm, mo):
    cid, run1 = make_case(client, anm)
    inp = review(client, mo, cid)["latest"]["input"]
    assert withdraw(client, anm, cid, "triage").status_code == 200
    r = correct(client, mo, cid, run1, with_vitals(inp, spo2=90))
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_provenance_and_history_show_only_what_the_server_recorded(client, anm, mo):
    cid, run1 = make_case(client, anm)
    assert review(client, mo, cid)["latest"]["field_provenance"] == {}  # intake run: no per-field source stored
    inp = review(client, mo, cid)["latest"]["input"]
    run2 = correct(client, mo, cid, run1, with_vitals(inp, spo2=90), "remeasured").json()["triage_run_id"]
    cv = review(client, mo, cid)
    prov = cv["latest"]["field_provenance"]
    assert prov["vitals.spo2"]["source"] == "reviewer_correction" and prov["vitals.spo2"]["previous_value"] == inp["vitals"]["spo2"]
    assert prov["vitals.spo2"]["is_current_user"] is True and prov["vitals.spo2"]["reason_label"] == review_queue.CORRECTION_REASONS["remeasured"]
    assert prov["*"] == {"source": "unchanged_from_previous_run", "from_run_id": run1}
    assert set(prov) == {"vitals.spo2", "*"}
    [c] = cv["corrections"]
    assert c["triage_run_id"] == run2 and c["changes"] == [{"field": "vitals.spo2", "old": inp["vitals"]["spo2"], "new": 90}]
    assert [r["corrects_run_id"] for r in cv["runs"]] == [None, run1]
    assert {r["code"] for r in cv["correction_reasons"]} == set(review_queue.CORRECTION_REASONS)
    # Consent withdrawn: values and free text are no longer served; the fact of the correction stays.
    assert withdraw(client, anm, cid, "triage").status_code == 200
    cv = review(client, mo, cid)
    assert cv["latest"]["field_provenance"] is None and cv["corrections"][0]["changes"] is None and cv["corrections"][0]["changed_fields"] == ["vitals.spo2"]


def test_correcting_a_red_to_green_does_not_close_the_red_escalation(client, anm, mo):
    cid, run1 = make_case(client, anm, **RED)
    inp = review(client, mo, cid)["latest"]["input"]
    run2 = correct(client, mo, cid, run1, with_vitals(inp, resp_rate=16), "entry_error").json()["triage_run_id"]
    item = next(i for i in client.get(f"{API}/triage/queue", headers=auth(mo)).json()["items"] if i["case_id"] == cid)
    assert item["triage_run_id"] == run2 and item["rules_urgency"] != "RED"
    assert item["escalation"]["state"] in ("pending", "overdue") and item["escalation"]["anchor_source"] == "earlier_red_run"


def test_governance_unaffected_by_corrections(client, anm, mo, sup):
    cid, run1 = make_case(client, anm)
    assert client.patch(f"{API}/triage/{cid}/override", json={"triage_run_id": run1, "new_urgency": "RED", "reason_code": "clinical_reassessment", "confirm": True},
                        headers=auth(mo)).status_code == 200
    inp = review(client, mo, cid)["latest"]["input"]
    assert correct(client, mo, cid, run1, with_vitals(inp, spo2=90)).status_code == 201
    g = client.get(f"{API}/audit/governance", headers=auth(sup)).json()
    assert g["overrides"]["events"] == 1 and g["overrides"]["cases_with_override"] == 1 and g["sample_size"] == 1
