"""Phase 8 reviewer dashboard API (docs/17): queue, case review, sign-off, override, RED escalation, governance.

Synthetic data only; no AI provider is involved (the queue never reads LLM output).
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import review_queue
from app.auth import Role, create_access_token
from app.config import get_settings
from tests.privacy.helpers import audit_rows, auth, grant, new_case, token_for, triage, withdraw

RED = {"vitals": {"resp_rate": 30}}
YELLOW = {"vitals": {"spo2": None}}  # missing vital → insufficient data → YELLOW, needs human review
GREEN: dict = {}
API = "/api/v1"


@pytest.fixture
def anm(client):
    return token_for(client, "anm")


@pytest.fixture
def mo(client):
    return token_for(client, "mo")


@pytest.fixture
def sup(client):
    return token_for(client, "supervisor")


class Clock:
    """Shifts 'now' for both the review service and case triage (so run timestamps move too)."""

    def __init__(self, monkeypatch):
        self.offset = timedelta(0)
        clock = self

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + clock.offset

        monkeypatch.setattr(review_queue, "_now", lambda: datetime.now(timezone.utc) + clock.offset)
        monkeypatch.setattr("app.case_triage.datetime", _DT)

    def advance(self, seconds: float) -> None:
        self.offset += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


def make_case(client, anm, inputs: dict) -> tuple[str, str]:
    cid = new_case(client, anm)
    assert grant(client, anm, cid).status_code == 200
    r = triage(client, anm, cid, **inputs)
    assert r.status_code == 200, r.text
    return cid, r.json()["run_id"]


def rerun(client, tok, cid, inputs: dict) -> str:
    r = triage(client, tok, cid, **inputs)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def q(client, tok, **params):
    return client.get(f"{API}/triage/queue", params=params, headers=auth(tok))


def review(client, tok, cid):
    return client.get(f"{API}/triage/{cid}", headers=auth(tok))


def sign_off(client, tok, cid, run_id, **extra):
    return client.patch(f"{API}/triage/{cid}/sign-off", json={"triage_run_id": run_id, "confirm": True, **extra}, headers=auth(tok))


def override(client, tok, cid, run_id, new, code="clinical_reassessment", text=None, **extra):
    body = {"triage_run_id": run_id, "new_urgency": new, "reason_code": code, "confirm": True, **extra}
    if text is not None:
        body["reason_text"] = text
    return client.patch(f"{API}/triage/{cid}/override", json=body, headers=auth(tok))


def ack(client, tok, cid, run_id):
    return client.post(f"{API}/triage/{cid}/acknowledge", json={"triage_run_id": run_id}, headers=auth(tok))


def db():
    c = sqlite3.connect(get_settings().database_path)
    c.row_factory = sqlite3.Row
    return c


def item_for(data, cid):
    return next(i for i in data["items"] if i["case_id"] == cid)


# ── Queue ────────────────────────────────────────────────────────────────────────────────────────


def test_queue_orders_red_yellow_green_then_oldest_first(client, anm, mo):
    g, _ = make_case(client, anm, GREEN)
    y, _ = make_case(client, anm, YELLOW)
    r1, _ = make_case(client, anm, RED)
    r2, _ = make_case(client, anm, RED)
    data = q(client, mo).json()
    assert [i["case_id"] for i in data["items"]] == [r1, r2, y, g]
    assert [i["priority_urgency"] for i in data["items"]] == ["RED", "RED", "YELLOW", "GREEN"]
    assert data["counts"] == {"RED": 2, "YELLOW": 1, "GREEN": 1}
    assert "_newly_overdue" not in data
    assert item_for(data, y)["determination"] == "insufficient_data" and item_for(data, y)["needs_human_review"] is True


def test_cases_with_absent_or_failed_ai_extraction_still_appear(client, anm, mo, sup):
    absent, absent_run = make_case(client, anm, YELLOW)
    failed, failed_run = make_case(client, anm, GREEN)
    with db() as c:  # a synthetic extraction that failed to reach agreement
        seq = c.execute("SELECT MAX(seq) FROM consent_events WHERE case_id = ?", (failed,)).fetchone()[0]
        c.execute(
            "INSERT INTO ai_extraction_runs (extraction_id, case_id, created_by, actor_role, idempotency_key, provider, provider_kind, model_id, prompt_version, "
            "schema_version, passes_requested, passes_valid, status, consent_seq, segments_json, skipped_json, dropped_json, abstentions_json, urgency_json, "
            "flags_json, created_at, request_sha256) VALUES (?, ?, 'x', 'anm', 'k', 'fake', 'fake', 'm', 'p', 's', 3, 0, 'insufficient_agreement', ?, "
            "'[]', '[]', '[]', '[]', '{}', '{}', '2026-01-01T00:00:00+00:00', 'h')",
            (str(uuid.uuid4()), failed, seq),
        )
        assert c.execute("SELECT COUNT(*) FROM ai_extraction_runs WHERE case_id = ?", (absent,)).fetchone()[0] == 0
    for tok in (mo, sup):
        data = q(client, tok).json()
        assert item_for(data, absent)["triage_run_id"] == absent_run and item_for(data, failed)["triage_run_id"] == failed_run


def test_queue_roles(client, anm):
    assert q(client, anm).status_code == 403
    assert q(client, token_for(client, "patient")).status_code == 403


def test_facility_filter(client, anm, mo):
    cid, _ = make_case(client, anm, GREEN)
    assert [i["case_id"] for i in q(client, mo, facility_code="PHC-KHURDA-01").json()["items"]] == [cid]
    assert q(client, mo, facility_code="PHC-OTHER-99").json()["items"] == []


def test_signed_off_leaves_default_queue_but_unacknowledged_red_stays(client, anm, mo):
    g, g_run = make_case(client, anm, GREEN)
    r, _ = make_case(client, anm, RED)
    assert sign_off(client, mo, g, g_run).status_code == 200
    ids = [i["case_id"] for i in q(client, mo).json()["items"]]
    assert g not in ids and r in ids
    assert item_for(q(client, mo, include_signed_off="true").json(), g)["review_status"] == "signed_off"


# ── Sign-off ─────────────────────────────────────────────────────────────────────────────────────


def test_sign_off_requires_confirm_true(client, anm, mo):
    cid, run_id = make_case(client, anm, GREEN)
    for body in ({"triage_run_id": run_id}, {"triage_run_id": run_id, "confirm": False}):
        r = client.patch(f"{API}/triage/{cid}/sign-off", json=body, headers=auth(mo))
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_sign_off_is_medical_officer_only(client, anm, mo, sup):
    cid, run_id = make_case(client, anm, GREEN)
    assert sign_off(client, anm, cid, run_id).status_code == 403
    assert sign_off(client, sup, cid, run_id).status_code == 403
    r = sign_off(client, mo, cid, run_id)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["triage_run_id"] == run_id and v["reviewer_role"] == "medical_officer" and v["rules_urgency"] == v["effective_urgency"] == "GREEN"
    assert review(client, mo, cid).json()["latest"]["review_status"] == "signed_off"
    assert any(a["action"] == "review_signed_off" and a["case_id"] == cid for a in audit_rows(client))


def test_duplicate_sign_off_and_stale_run_are_409(client, anm, mo):
    cid, run1 = make_case(client, anm, GREEN)
    assert sign_off(client, mo, cid, run1).status_code == 200
    r = sign_off(client, mo, cid, run1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_SIGNED_OFF"
    run2 = rerun(client, anm, cid, YELLOW)
    r = sign_off(client, mo, cid, run1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN"
    assert r.json()["error"]["details"]["latest_triage_run_id"] == run2
    assert review(client, mo, cid).json()["latest"]["review_status"] == "awaiting_review"  # new run → fresh review
    with db() as c:
        assert c.execute("SELECT COUNT(*) FROM review_events WHERE kind = 'sign_off'").fetchone()[0] == 1


def test_unique_index_enforces_one_sign_off_per_run(client, anm, mo):
    cid, run_id = make_case(client, anm, GREEN)
    assert sign_off(client, mo, cid, run_id).status_code == 200
    with db() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO review_events (event_id, case_id, triage_run_id, kind, rules_urgency, actor_id, actor_role, created_at) "
                  "VALUES (?, ?, ?, 'sign_off', 'GREEN', 'x', 'medical_officer', 'now')", (str(uuid.uuid4()), cid, run_id))


def test_sign_off_after_triage_consent_withdrawn_is_403(client, anm, mo):
    cid, run_id = make_case(client, anm, GREEN)
    assert withdraw(client, anm, cid, "triage").status_code == 200
    r = sign_off(client, mo, cid, run_id)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_REQUIRED"


# ── Override ─────────────────────────────────────────────────────────────────────────────────────


def test_override_validation(client, anm, mo):
    cid, run_id = make_case(client, anm, GREEN)
    assert override(client, mo, cid, run_id, "YELLOW", code="gut_feeling").status_code == 400
    assert override(client, mo, cid, run_id, "YELLOW", code="other").status_code == 400
    assert override(client, mo, cid, run_id, "YELLOW", code="other", text="   too short").status_code == 400
    assert override(client, mo, cid, run_id, "YELLOW", confirm=False).status_code == 400
    r = override(client, mo, cid, run_id, "GREEN")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_CHANGE"
    assert override(client, anm, cid, run_id, "YELLOW").status_code == 403
    r = override(client, mo, cid, run_id, "YELLOW", code="other", text="Patient reports worsening since intake")
    assert r.status_code == 200, r.text
    assert r.json()["old_urgency"] == "GREEN" and r.json()["new_urgency"] == "YELLOW" and r.json()["rules_urgency"] == "GREEN"
    details = json.loads(next(a for a in audit_rows(client) if a["action"] == "urgency_overridden")["details_json"])
    assert details["has_reason_text"] is True and "worsening" not in json.dumps(details)


def test_override_never_modifies_triage_runs_and_is_append_only(client, anm, mo):
    cid, run_id = make_case(client, anm, RED)
    assert override(client, mo, cid, run_id, "GREEN", code="data_entry_error").status_code == 200
    with db() as c:
        assert c.execute("SELECT urgency FROM triage_runs WHERE run_id = ?", (run_id,)).fetchone()[0] == "RED"
        assert json.loads(c.execute("SELECT result_json FROM triage_runs WHERE run_id = ?", (run_id,)).fetchone()[0])["urgency"] == "RED"
        for stmt in ("UPDATE review_events SET new_urgency = 'RED'", "DELETE FROM review_events"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                c.execute(stmt)


def test_lowering_override_does_not_drop_queue_priority(client, anm, mo):
    y, _ = make_case(client, anm, YELLOW)
    r, r_run = make_case(client, anm, RED)
    assert override(client, mo, r, r_run, "GREEN", code="clinical_reassessment").status_code == 200
    data = q(client, mo).json()
    assert [i["case_id"] for i in data["items"]] == [r, y]
    item = item_for(data, r)
    assert item["priority_urgency"] == "RED" and item["priority_source"] == "rules_engine"
    assert item["effective_urgency"] == "GREEN" and item["effective_urgency_source"] == "reviewer_override" and item["rules_urgency"] == "RED"
    assert item["escalation"] is not None  # the rules RED escalation is not cancelled by a lowering override


def test_raising_override_raises_priority_and_starts_escalation(client, anm, mo, clock):
    g, g_run = make_case(client, anm, GREEN)
    y, _ = make_case(client, anm, YELLOW)
    assert override(client, mo, g, g_run, "RED", code="additional_information").status_code == 200
    data = q(client, mo).json()
    assert [i["case_id"] for i in data["items"]] == [g, y]
    esc = item_for(data, g)["escalation"]
    assert item_for(data, g)["priority_source"] == "reviewer_override_raise"
    assert esc["anchor_source"] == "reviewer_override" and esc["state"] == "pending"


# ── RED escalation ───────────────────────────────────────────────────────────────────────────────


def test_red_deadline_is_run_time_plus_180_and_goes_overdue_once(client, anm, mo, sup, clock):
    cid, run_id = make_case(client, anm, RED)
    esc = item_for(q(client, mo).json(), cid)["escalation"]
    with db() as c:
        run_at = c.execute("SELECT created_at FROM triage_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
    assert esc["anchor"] == run_at and esc["anchor_source"] == "triage_run"
    assert datetime.fromisoformat(esc["deadline"]) - datetime.fromisoformat(run_at) == timedelta(seconds=180)
    assert esc["state"] == "pending" and esc["notification_sent"] is False and esc["window_seconds"] == 180
    clock.advance(181)
    assert item_for(q(client, mo).json(), cid)["escalation"]["state"] == "overdue"
    q(client, sup)
    e = client.get(f"{API}/triage/escalations", headers=auth(mo)).json()
    assert item_for(e, cid)["escalation"]["state"] == "overdue" and e["notification_channel"] is None and "_newly_overdue" not in e
    overdue = [a for a in audit_rows(client) if a["action"] == "red_escalation_overdue"]
    assert len(overdue) == 1 and json.loads(overdue[0]["details_json"])["triage_run_id"] == run_id


def test_acknowledge(client, anm, mo, sup, clock):
    cid, run_id = make_case(client, anm, RED)
    assert ack(client, sup, cid, run_id).status_code == 403
    clock.advance(200)
    r = ack(client, mo, cid, run_id)
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged_late"] is True and r.json()["notification_sent"] is False
    r = ack(client, mo, cid, run_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_ACKNOWLEDGED"
    esc = item_for(q(client, mo).json(), cid)["escalation"]
    assert esc["state"] == "acknowledged" and esc["acknowledged_via"] == "acknowledge" and esc["acknowledged_late"] is True
    assert not [a for a in audit_rows(client) if a["action"] == "red_escalation_overdue"]  # acknowledged before any observation


def test_acknowledge_non_red_is_409(client, anm, mo):
    cid, run_id = make_case(client, anm, YELLOW)
    r = ack(client, mo, cid, run_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ESCALATED"


def test_sign_off_acknowledges_red_escalation(client, anm, mo):
    cid, run_id = make_case(client, anm, RED)
    assert sign_off(client, mo, cid, run_id).status_code == 200
    esc = review(client, mo, cid).json()["latest"]["escalation"]
    assert esc["state"] == "acknowledged" and esc["acknowledged_via"] == "sign_off" and esc["acknowledged_late"] is False
    assert any(a["action"] == "red_escalation_acknowledged" for a in audit_rows(client))


def test_retriage_does_not_restart_red_clock(client, anm, mo, clock):
    cid, run1 = make_case(client, anm, RED)
    clock.advance(200)
    run2 = rerun(client, anm, cid, RED)
    esc = item_for(q(client, mo).json(), cid)["escalation"]
    with db() as c:
        run1_at = c.execute("SELECT created_at FROM triage_runs WHERE run_id = ?", (run1,)).fetchone()[0]
    assert esc["state"] == "overdue" and esc["anchor"] == run1_at and esc["anchor_source"] == "earlier_red_run"
    assert item_for(q(client, mo).json(), cid)["triage_run_id"] == run2
    # acknowledgment binds to the latest run
    assert ack(client, mo, cid, run1).json()["error"]["code"] == "STALE_TRIAGE_RUN"
    assert ack(client, mo, cid, run2).status_code == 200


def test_acknowledgment_closes_red_so_a_later_red_starts_a_new_clock(client, anm, mo, clock):
    a, a1 = make_case(client, anm, RED)
    assert ack(client, mo, a, a1).status_code == 200
    clock.advance(200)
    rerun(client, anm, a, RED)  # previous RED was acknowledged → new anchor
    esc = item_for(q(client, mo).json(), a)["escalation"]
    assert esc["state"] == "pending" and esc["anchor_source"] == "triage_run"


def test_non_red_rerun_neither_closes_nor_restarts_an_unacknowledged_red(client, anm, mo, sup, clock):
    """Agent D P1: a GREEN/YELLOW re-run used to make an overdue, unacknowledged RED disappear, and RED→GREEN→RED
    restarted the clock. Only an acknowledgment or sign-off closes it."""
    b, b1 = make_case(client, anm, RED)
    clock.advance(200)
    b2 = rerun(client, anm, b, GREEN)
    with db() as c:
        b1_at = c.execute("SELECT created_at FROM triage_runs WHERE run_id = ?", (b1,)).fetchone()[0]
    item = item_for(q(client, mo).json(), b)
    assert item["rules_urgency"] == "GREEN"
    assert item["escalation"]["state"] == "overdue" and item["escalation"]["anchor"] == b1_at and item["escalation"]["anchor_source"] == "earlier_red_run"
    assert sup_red_cases(client, sup) >= 1
    rerun(client, anm, b, RED)  # RED → GREEN → RED keeps the original anchor
    item = item_for(q(client, mo).json(), b)
    assert item["escalation"]["state"] == "overdue" and item["escalation"]["anchor"] == b1_at
    b3 = item["triage_run_id"]
    assert ack(client, mo, b, b2).json()["error"]["code"] == "STALE_TRIAGE_RUN"
    assert ack(client, mo, b, b3).status_code == 200
    item = item_for(q(client, mo, include_signed_off="true").json(), b)
    assert item["escalation"]["state"] == "acknowledged" and item["escalation"]["acknowledged_late"] is True


def test_acknowledging_a_carried_red_on_a_non_red_run_closes_it(client, anm, mo, clock):
    c_, c1 = make_case(client, anm, RED)
    c2 = rerun(client, anm, c_, GREEN)
    assert ack(client, mo, c_, c2).status_code == 200
    rerun(client, anm, c_, GREEN)
    assert item_for(q(client, mo, include_signed_off="true").json(), c_)["escalation"] is None


def sup_red_cases(client, sup) -> int:
    return client.get(f"{API}/audit/governance", headers=auth(sup)).json()["red_escalation"]["red_cases"]


# ── Case review ──────────────────────────────────────────────────────────────────────────────────


def test_case_review_shape_and_supervisor_is_read_only(client, anm, mo, sup):
    cid, run_id = make_case(client, anm, RED)
    v = review(client, mo, cid).json()
    assert v["can_review"] is True and v["clinical_content_available"] is True
    assert v["latest"]["triage_run_id"] == run_id and v["latest"]["result"]["urgency"] == "RED"
    assert v["latest"]["input"]["vitals"]["resp_rate"] == 30 and v["latest"]["has_input"] is True
    assert {r["code"] for r in v["override_reasons"]} == set(review_queue.OVERRIDE_REASONS)
    assert v["consent"]["triage"] == "granted" and v["audit"] and "actor_id" not in v["audit"][0]
    s = review(client, sup, cid)
    assert s.status_code == 200 and s.json()["can_review"] is False
    assert review(client, anm, cid).status_code == 403
    assert review(client, mo, str(uuid.uuid4())).status_code == 404


def test_consent_withdrawn_hides_clinical_content_but_keeps_urgency_and_escalation(client, anm, mo):
    cid, run_id = make_case(client, anm, RED)
    assert override(client, mo, cid, run_id, "YELLOW", code="other", text="Reassessed at bedside, stable").status_code == 200
    assert withdraw(client, anm, cid, "triage").status_code == 200
    v = review(client, mo, cid).json()
    assert v["clinical_content_available"] is False and v["consent"]["triage"] == "withdrawn"
    latest = v["latest"]
    assert latest["result"] is None and latest["input"] is None
    assert latest["missing_fields"] == [] and latest["triggered_rule_ids"] == []
    assert latest["rules_urgency"] == "RED" and latest["priority_urgency"] == "RED" and latest["escalation"]["state"] == "pending"
    assert v["review_events"][0]["reason_text"] is None and v["review_events"][0]["reason_code"] == "other"
    queued = item_for(q(client, mo).json(), cid)
    assert queued["consent_triage"] == "withdrawn" and queued["triggered_rule_ids"] == []
    # acknowledgment processes no clinical data and stays possible
    assert ack(client, mo, cid, run_id).status_code == 200


# ── Governance ───────────────────────────────────────────────────────────────────────────────────


def test_governance_empty_rates_are_null(client, sup):
    v = client.get(f"{API}/audit/governance", headers=auth(sup)).json()
    assert v["sample_size"] == 0 and v["small_sample_warning"] is True
    assert v["review_completion"]["rate"] is None and v["overrides"]["rate"] is None and v["insufficient_data"]["rate"] is None
    assert v["turnaround_seconds"]["median"] is None and v["ai_field_reviews"]["disagreement_rate"] is None


def test_governance_roles_and_route_order(client, anm, mo, sup):
    cid, _ = make_case(client, anm, GREEN)
    assert client.get(f"{API}/audit/governance", headers=auth(mo)).status_code == 403
    assert client.get(f"{API}/audit/governance", headers=auth(token_for(client, "anm"))).status_code == 403
    admin = create_access_token("admin_test", Role.ADMIN)[0]  # no admin demo account exists; token minted for the role check
    assert client.get(f"{API}/audit/governance", headers=auth(admin)).status_code == 200
    assert client.get(f"{API}/audit/governance", headers=auth(sup)).status_code == 200
    assert client.get(f"{API}/audit/{cid}", headers=auth(sup)).json()["case_id"] == cid  # case audit route still reachable


def test_governance_denominators_and_privacy(client, anm, mo, sup):
    g, g_run = make_case(client, anm, GREEN)
    y, _ = make_case(client, anm, YELLOW)
    r, r_run = make_case(client, anm, RED)
    assert sign_off(client, mo, g, g_run).status_code == 200
    assert override(client, mo, r, r_run, "YELLOW", code="source_disputed").status_code == 200
    v = client.get(f"{API}/audit/governance", headers=auth(sup)).json()
    assert v["sample_size"] == 3
    assert v["review_completion"] == {**v["review_completion"], "signed_off": 1, "denominator": 3, "rate": round(1 / 3, 4)}
    o = v["overrides"]
    assert o["cases_with_override"] == 1 and o["denominator"] == 3 and o["events"] == 1 and o["lowered"] == 1 and o["raised"] == 0
    assert {x["code"]: x["count"] for x in o["reasons"]}["source_disputed"] == 1
    assert v["insufficient_data"]["count"] == 1 and v["red_escalation"]["red_cases"] == 1
    assert v["turnaround_seconds"]["n"] == 1
    blob = json.dumps(v)
    with db() as c:
        tokens = [row[0] for row in c.execute("SELECT patient_token FROM cases")]
    for secret in (g, y, r, g_run, r_run, *tokens):
        assert secret not in blob


def test_governance_override_survives_retriage(client, anm, mo, sup):
    cid, run1 = make_case(client, anm, GREEN)
    assert override(client, mo, cid, run1, "YELLOW", code="clinical_reassessment").status_code == 200
    rerun(client, anm, cid, GREEN)
    o = client.get(f"{API}/audit/governance", headers=auth(sup)).json()["overrides"]
    assert o["cases_with_override"] == 1 and o["events"] == 1 and o["raised"] == 1


def test_governance_period_filter_handles_timezones(client, anm, sup):
    make_case(client, anm, GREEN)
    now = datetime.now(timezone.utc)
    ist = timezone(timedelta(hours=5, minutes=30))
    in_window = {"since": (now - timedelta(minutes=5)).astimezone(ist).isoformat(), "until": (now + timedelta(minutes=5)).astimezone(ist).isoformat()}
    future = {"since": (now + timedelta(hours=1)).isoformat()}
    assert client.get(f"{API}/audit/governance", params=in_window, headers=auth(sup)).json()["sample_size"] == 1
    assert client.get(f"{API}/audit/governance", params=future, headers=auth(sup)).json()["sample_size"] == 0


def test_override_reason_with_identifier_is_rejected(client, anm, mo):
    cid, run_id = make_case(client, anm, GREEN)
    r = override(client, mo, cid, run_id, "YELLOW", code="other", text="Spoke to Ramesh Kumar, call 9876543210")
    assert r.status_code == 422 and r.json()["error"]["code"] == "PII_DETECTED" and "Ramesh" not in r.text
    with db() as c:
        assert c.execute("SELECT COUNT(*) FROM review_events").fetchone()[0] == 0


def test_reviewer_raised_red_survives_a_corrected_rerun(client, anm, mo, clock):
    """Final council (Contrarian): a reviewer-raised RED used to vanish when anyone re-ran triage."""
    cid, run1 = make_case(client, anm, GREEN)
    assert override(client, mo, cid, run1, "RED").status_code == 200
    clock.advance(200)
    rerun(client, mo, cid, GREEN)
    item = item_for(q(client, mo).json(), cid)
    assert item["rules_urgency"] == "GREEN"
    assert item["escalation"] is not None and item["escalation"]["state"] == "overdue" and item["escalation"]["anchor_source"] == "earlier_red_run"
    assert ack(client, mo, cid, item["triage_run_id"]).status_code == 200


# ── Phase 8 hardening regressions (docs/17 §10) ─────────────────────────────────────────────────────────────

def test_lowering_after_a_reviewer_raise_does_not_close_the_red(client, anm, mo, sup, clock):
    """Agents A/B P1: a later override that lowers a reviewer-raised RED used to clear the escalation silently."""
    cid, run1 = make_case(client, anm, GREEN)
    assert override(client, mo, cid, run1, "RED").status_code == 200
    assert override(client, mo, cid, run1, "GREEN", code="data_entry_error").status_code == 200
    clock.advance(400)
    item = item_for(q(client, mo).json(), cid)
    assert item["escalation"]["state"] == "overdue" and item["escalation"]["anchor_source"] == "reviewer_override"
    assert item["priority_urgency"] == "RED" and item["priority_source"] == "open_red_escalation"
    assert client.get(f"{API}/audit/governance", headers=auth(sup)).json()["red_escalation"]["overdue_unacknowledged"] == 1
    assert ack(client, mo, cid, run1).status_code == 200
    item = item_for(q(client, mo, include_signed_off="true").json(), cid)
    assert item["escalation"]["state"] == "acknowledged" and item["priority_urgency"] == "RED"  # "seen" does not lower priority
    assert sign_off(client, mo, cid, run1).status_code == 200
    assert item_for(q(client, mo, include_signed_off="true").json(), cid)["priority_urgency"] == "GREEN"


def test_carried_red_keeps_red_priority_until_signed_off(client, anm, mo):
    cid, _ = make_case(client, anm, RED)
    run2 = rerun(client, anm, cid, GREEN)
    item = item_for(q(client, mo).json(), cid)
    assert item["rules_urgency"] == "GREEN" and item["priority_urgency"] == "RED" and item["priority_source"] == "open_red_escalation"
    assert ack(client, mo, cid, run2).status_code == 200
    assert item_for(q(client, mo).json(), cid)["priority_urgency"] == "RED"
    assert sign_off(client, mo, cid, run2).status_code == 200
    assert item_for(q(client, mo, include_signed_off="true").json(), cid)["priority_urgency"] == "GREEN"


def test_one_overdue_audit_event_per_escalation_across_reruns(client, anm, mo, clock):
    """Agent A V2: the overdue event was re-recorded on every re-run of a carried RED."""
    cid, _ = make_case(client, anm, RED)
    clock.advance(200)
    q(client, mo)
    rerun(client, anm, cid, GREEN)
    q(client, mo)
    rerun(client, anm, cid, YELLOW)
    q(client, mo)
    assert sum(1 for a in audit_rows(client) if a["action"] == "red_escalation_overdue" and a["case_id"] == cid) == 1


def test_governance_keeps_red_history_after_rerun(client, anm, mo, sup, clock):
    """Agent A V3: an acknowledged-late RED vanished from governance once triage was re-run."""
    cid, run1 = make_case(client, anm, RED)
    clock.advance(200)
    assert ack(client, mo, cid, run1).status_code == 200
    rerun(client, anm, cid, GREEN)
    red = client.get(f"{API}/audit/governance", headers=auth(sup)).json()["red_escalation"]
    assert (red["red_cases"], red["episodes"], red["acknowledged_late"], red["acknowledged_within_window"]) == (1, 1, 1, 0)
    rerun(client, anm, cid, RED)  # a NEW RED after acknowledgment is a second episode
    red = client.get(f"{API}/audit/governance", headers=auth(sup)).json()["red_escalation"]
    assert (red["red_cases"], red["episodes"], red["pending"]) == (1, 2, 1)


def test_override_with_stale_expected_urgency_is_refused(client, anm, mo):
    cid, run1 = make_case(client, anm, GREEN)
    assert override(client, mo, cid, run1, "RED").status_code == 200  # reviewer A
    r = override(client, mo, cid, run1, "YELLOW", expected_urgency="GREEN")  # reviewer B saw GREEN
    assert r.status_code == 409 and r.json()["error"]["code"] == "URGENCY_CHANGED" and r.json()["error"]["details"]["current_urgency"] == "RED"
    assert override(client, mo, cid, run1, "YELLOW", expected_urgency="RED").status_code == 200


def test_override_on_a_superseded_run_is_stale(client, anm, mo):
    cid, run1 = make_case(client, anm, GREEN)
    rerun(client, anm, cid, YELLOW)
    r = override(client, mo, cid, run1, "RED")
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN"


@pytest.mark.parametrize("mutate, field", [
    (lambda b: b.pop("reason_code"), "reason_code"),
    (lambda b: b.update(new_urgency="PURPLE"), "new_urgency"),
    (lambda b: b.update(reason_text=5), "reason_text"),
    (lambda b: b.update(reason_text="x" * 501), "reason_text"),
    (lambda b: b.update(confirm="true"), "confirm"),
    (lambda b: b.update(triage_run_id="nope"), "triage_run_id"),
    (lambda b: b.update(reviewer_id="DR-SOMEONE"), "<extra>"),
    (lambda b: b.update(reviewer_name="Dr Someone"), "<extra>"),
])
def test_override_malformed_payloads_are_rejected_server_side(client, anm, mo, mutate, field):
    cid, run1 = make_case(client, anm, GREEN)
    body = {"triage_run_id": run1, "new_urgency": "YELLOW", "reason_code": "clinical_reassessment", "confirm": True}
    mutate(body)
    r = client.patch(f"{API}/triage/{cid}/override", json=body, headers=auth(mo))
    assert r.status_code == 400 and field in [e["field"] for e in r.json()["error"]["details"]["errors"]]


def test_supervisor_cannot_override(client, anm, mo, sup):
    cid, run1 = make_case(client, anm, GREEN)
    assert override(client, sup, cid, run1, "RED").status_code == 403


def test_reviewer_identity_comes_from_the_token_only(client, anm, mo):
    from app.auth import demo_user_id

    cid, run1 = make_case(client, anm, GREEN)
    # A header naming another user is refused, not trusted.
    r = client.patch(f"{API}/triage/{cid}/sign-off", json={"triage_run_id": run1, "confirm": True},
                     headers={**auth(mo), "X-SEHAT-User-ID": demo_user_id("anm_demo")})
    assert r.status_code == 403
    r = client.patch(f"{API}/triage/{cid}/sign-off", json={"triage_run_id": run1, "confirm": True}, headers={**auth(mo), "X-SEHAT-Role": "supervisor"})
    assert r.status_code == 403
    assert sign_off(client, mo, cid, run1).status_code == 200
    with db() as c:
        ev = c.execute("SELECT actor_id, actor_role FROM review_events WHERE triage_run_id = ?", (run1,)).fetchone()
    assert (ev["actor_id"], ev["actor_role"]) == (demo_user_id("mo_demo"), "medical_officer")
    row = next(a for a in audit_rows(client) if a["action"] == "review_signed_off" and a["case_id"] == cid)
    assert row["actor_id"] != "" and row["actor_role"] == "medical_officer"
    # actor_id is never served to the browser.
    assert "actor_id" not in json.dumps(review(client, mo, cid).json())


def test_a_new_raise_after_acknowledgment_opens_a_new_escalation(client, anm, mo, sup, clock):
    """Final council (Contrarian): raise → ack → lower → raise again on ONE run was reported as already acknowledged."""
    cid, run1 = make_case(client, anm, GREEN)
    assert override(client, mo, cid, run1, "RED").status_code == 200
    assert ack(client, mo, cid, run1).status_code == 200
    assert override(client, mo, cid, run1, "GREEN", code="data_entry_error").status_code == 200
    clock.advance(30)
    assert override(client, mo, cid, run1, "RED").status_code == 200
    esc = item_for(q(client, mo).json(), cid)["escalation"]
    assert esc["state"] == "pending" and esc["anchor_source"] == "reviewer_override" and esc["acknowledged_at"] is None
    clock.advance(200)
    assert item_for(q(client, mo).json(), cid)["escalation"]["state"] == "overdue"
    assert sum(1 for a in audit_rows(client) if a["action"] == "red_escalation_overdue" and a["case_id"] == cid) == 1
    red = client.get(f"{API}/audit/governance", headers=auth(sup)).json()["red_escalation"]
    assert (red["episodes"], red["acknowledged"], red["overdue_unacknowledged"]) == (2, 1, 1)
    assert ack(client, mo, cid, run1).status_code == 200  # the second escalation gets its own acknowledgment
    r = ack(client, mo, cid, run1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_ACKNOWLEDGED"  # but never twice
    esc = item_for(q(client, mo, include_signed_off="true").json(), cid)["escalation"]
    assert esc["state"] == "acknowledged" and esc["acknowledged_late"] is True


def test_rerunning_an_unacknowledged_red_is_one_governance_episode(client, anm, mo, sup):
    cid, _ = make_case(client, anm, RED)
    rerun(client, anm, cid, RED)
    rerun(client, anm, cid, RED)
    red = client.get(f"{API}/audit/governance", headers=auth(sup)).json()["red_escalation"]
    assert (red["red_cases"], red["episodes"], red["pending"]) == (1, 1, 1)
