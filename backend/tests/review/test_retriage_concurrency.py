"""Intake re-triage optimistic concurrency (docs/17 §3b). Synthetic data only.

Concurrency tests open two real aiosqlite connections to the same database file, so the requests genuinely contend
for SQLite's write lock (BEGIN IMMEDIATE); a sync TestClient would only run them one after another.
"""

import asyncio
import sqlite3
import uuid

import pytest

from app import case_triage, review_queue
from app.auth import Principal, Role, demo_user_id
from app.config import get_settings
from app.database import _connect
from app.errors import ApiError
from app.rules import TriageInput
from tests.privacy.helpers import audit_rows, auth, grant, latest_run_id, new_case, token_for, triage, withdraw
from tests.rules.vignettes import case as triage_case

API = "/api/v1"
RED = {"vitals": {"resp_rate": 30}}


@pytest.fixture
def anm(client):
    return token_for(client, "anm")


@pytest.fixture
def mo(client):
    return token_for(client, "mo")


def make_case(client, anm, **inputs) -> tuple[str, str]:
    cid = new_case(client, anm)
    assert grant(client, anm, cid).status_code == 200
    r = triage(client, anm, cid, expected=None, **inputs)  # first run: no token needed
    assert r.status_code == 200, r.text
    return cid, r.json()["run_id"]


def run_count(cid: str) -> int:
    with sqlite3.connect(get_settings().database_path) as c:
        return c.execute("SELECT COUNT(*) FROM triage_runs WHERE case_id = ?", (cid,)).fetchone()[0]


ANM = Principal(user_id=demo_user_id("anm_demo"), username="anm_demo", role=Role.ANM)
MO = Principal(user_id=demo_user_id("mo_demo"), username="mo_demo", role=Role.MEDICAL_OFFICER)


def run_concurrently(*coros_factories):
    """Each factory gets its own connection; results are values or the raised exception."""
    path = get_settings().database_path

    async def one(factory):
        conn = await _connect(path)
        try:
            return await factory(conn)
        except Exception as exc:  # noqa: BLE001 — the loser's error is the assertion target
            return exc
        finally:
            await conn.close()

    async def all_():
        return await asyncio.gather(*(one(f) for f in coros_factories))

    return asyncio.run(all_())


def test_first_run_needs_no_token_and_case_view_exposes_the_latest_run(client, anm):
    cid, run1 = make_case(client, anm)
    assert client.get(f"{API}/cases/{cid}", headers=auth(anm)).json()["latest_triage_run_id"] == run1


@pytest.mark.parametrize("expected, code", [(None, "EXPECTED_RUN_REQUIRED"), ("none", "STALE_TRIAGE_RUN"), (str(uuid.uuid4()), "STALE_TRIAGE_RUN")])
def test_retriage_without_the_current_run_is_refused_and_writes_nothing(client, anm, expected, code):
    cid, run1 = make_case(client, anm)
    audit_before = len(audit_rows(client))
    r = triage(client, anm, cid, expected=expected, **RED)
    assert r.status_code == 409 and r.json()["error"]["code"] == code
    assert r.json()["error"]["details"]["latest_triage_run_id"] == run1
    assert run_count(cid) == 1 and len(audit_rows(client)) == audit_before


def test_a_run_id_token_on_a_case_without_runs_is_stale(client, anm):
    cid = new_case(client, anm)
    assert grant(client, anm, cid).status_code == 200
    r = triage(client, anm, cid, expected=str(uuid.uuid4()))
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN" and run_count(cid) == 0


def test_malformed_token_is_a_validation_error(client, anm):
    cid, _ = make_case(client, anm)
    r = client.post(f"{API}/cases/{cid}/triage", params={"expected_run_id": "latest"}, json=triage_case(), headers=auth(anm))
    assert r.status_code == 400 and run_count(cid) == 1


def test_two_concurrent_retriages_from_the_same_view_commit_exactly_once(client, anm):
    cid, run1 = make_case(client, anm)
    data = TriageInput.model_validate(triage_case(**RED))
    results = run_concurrently(*(lambda conn: case_triage.run_case_triage(conn, ANM, cid, data, None, run1) for _ in range(2)))
    ok = [r for r in results if isinstance(r, tuple)]
    errs = [r for r in results if isinstance(r, ApiError)]
    assert len(ok) == 1 and len(errs) == 1 and errs[0].code == "STALE_TRIAGE_RUN", results
    assert run_count(cid) == 2


def test_concurrent_retriage_and_correction_commit_exactly_once(client, anm, mo):
    cid, run1 = make_case(client, anm)
    stored = TriageInput.model_validate(client.get(f"{API}/triage/{cid}", headers=auth(mo)).json()["latest"]["input"])
    corrected = stored.model_copy(update={"vitals": stored.vitals.model_copy(update={"spo2": 90})})
    body = review_queue.CorrectionBody(expected_triage_run_id=uuid.UUID(run1), input=corrected, reason_code="remeasured", confirm=True)
    results = run_concurrently(
        lambda conn: case_triage.run_case_triage(conn, ANM, cid, TriageInput.model_validate(triage_case(**RED)), None, run1),
        lambda conn: review_queue.correct(conn, MO, cid, body, None),
    )
    winners = [r for r in results if not isinstance(r, Exception)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and len(losers) == 1 and losers[0].code == "STALE_TRIAGE_RUN", results
    assert run_count(cid) == 2


def test_retriage_after_an_override_keeps_history_and_a_reviewer_raised_red(client, anm, mo):
    cid, run1 = make_case(client, anm)
    assert client.patch(f"{API}/triage/{cid}/override", json={"triage_run_id": run1, "new_urgency": "RED", "reason_code": "clinical_reassessment", "confirm": True},
                        headers=auth(mo)).status_code == 200
    run2 = triage(client, anm, cid, expected=run1).json()["run_id"]  # the ANM saw run1; the override does not change the run
    cv = client.get(f"{API}/triage/{cid}", headers=auth(mo)).json()
    assert any(e["kind"] == "override" and e["triage_run_id"] == run1 for e in cv["review_events"])
    assert cv["latest"]["triage_run_id"] == run2
    assert cv["latest"]["priority_urgency"] == "RED" and cv["latest"]["escalation"]["anchor_source"] == "earlier_red_run"
    # The reviewer's next action on the superseded run is refused, never applied to run2 silently.
    r = client.patch(f"{API}/triage/{cid}/sign-off", json={"triage_run_id": run1, "confirm": True}, headers=auth(mo))
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN"


def test_lower_retriage_keeps_an_open_red(client, anm, mo):
    cid, run1 = make_case(client, anm, **RED)
    triage(client, anm, cid, expected=run1)  # GREEN vignette defaults
    item = next(i for i in client.get(f"{API}/triage/queue", headers=auth(mo)).json()["items"] if i["case_id"] == cid)
    assert item["priority_urgency"] == "RED" and item["escalation"]["state"] in ("pending", "overdue")


def test_failed_transactions_write_nothing(client, anm):
    cid, run1 = make_case(client, anm)
    r = client.post(f"{API}/cases/{cid}/triage", params={"expected_run_id": run1}, json=triage_case("maternal"), headers=auth(anm))
    assert r.status_code in (400, 409) and run_count(cid) == 1  # scenario mismatch / invalid for this case
    assert withdraw(client, anm, cid, "triage").status_code == 200
    r = triage(client, anm, cid, expected=run1)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_REQUIRED" and run_count(cid) == 1
    assert latest_run_id(cid) == run1


def test_review_actions_refuse_a_non_medical_officer_even_below_the_routes(client, anm):
    """Final council (Contrarian): defence in depth — the creating ANM cannot review its own case via the service layer."""
    cid, run1 = make_case(client, anm, **RED)
    body_ack = review_queue.AcknowledgeBody(triage_run_id=uuid.UUID(run1))
    body_sign = review_queue.SignOffBody(triage_run_id=uuid.UUID(run1), confirm=True)
    body_ovr = review_queue.OverrideBody(triage_run_id=uuid.UUID(run1), new_urgency="GREEN", reason_code="clinical_reassessment", confirm=True)
    body_cor = review_queue.CorrectionBody(expected_triage_run_id=uuid.UUID(run1), input=TriageInput.model_validate(triage_case()), reason_code="entry_error", confirm=True)
    results = run_concurrently(
        lambda conn: review_queue.acknowledge(conn, ANM, cid, body_ack, None),
        lambda conn: review_queue.sign_off(conn, ANM, cid, body_sign, None),
        lambda conn: review_queue.override(conn, ANM, cid, body_ovr, None),
        lambda conn: review_queue.correct(conn, ANM, cid, body_cor, None),
    )
    assert all(isinstance(r, ApiError) and r.status_code == 403 for r in results), results
    assert run_count(cid) == 1
    with sqlite3.connect(get_settings().database_path) as c:
        assert c.execute("SELECT COUNT(*) FROM review_events WHERE case_id = ?", (cid,)).fetchone()[0] == 0
