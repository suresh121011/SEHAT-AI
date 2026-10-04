"""Shared helpers for Phase 3 tests. Synthetic data only."""

from app.auth import Role, create_access_token
from app.consent_notice import NOTICE_VERSION
from tests.conftest import login
from tests.rules.vignettes import case as triage_case


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(client, who: str) -> str:
    return {
        "patient": lambda: login(client, "patient_demo", "patient"),
        "anm": lambda: login(client, "anm_demo", "anm"),
        "mo": lambda: login(client, "mo_demo", "medical_officer"),
        "supervisor": lambda: login(client, "supervisor_demo", "supervisor"),
        # A different account of the same role, to exercise per-creator checks in code.
        # (Real separation needs real accounts; the demo accounts are shared.)
        "anm_other": lambda: create_access_token("anm_other", Role.ANM)[0],
        "patient_other": lambda: create_access_token("patient_other", Role.PATIENT)[0],
    }[who]()


def new_case(client, token: str, scenario: str = "opd") -> str:
    resp = client.post("/api/v1/cases", json={"scenario": scenario, "facility_code": "PHC-KHURDA-01"}, headers=auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["case_id"]


def grant(client, token: str, case_id: str, ai: bool = False, language: str = "en", version: str = NOTICE_VERSION, voice_cloud: bool = False):
    return client.post(
        f"/api/v1/cases/{case_id}/consent",
        json={"decision": "grant", "include_ai_assist": ai, "include_voice_cloud": voice_cloud, "language": language, "notice_version": version},
        headers=auth(token),
    )


def decline(client, token: str, case_id: str):
    return client.post(
        f"/api/v1/cases/{case_id}/consent",
        json={"decision": "decline", "language": "en", "notice_version": NOTICE_VERSION},
        headers=auth(token),
    )


def withdraw(client, token: str, case_id: str, purpose: str):
    return client.post(f"/api/v1/cases/{case_id}/consent/withdraw", json={"purpose": purpose}, headers=auth(token))


AUTO = object()


def latest_run_id(case_id: str) -> str | None:
    import sqlite3

    from app.config import get_settings

    with sqlite3.connect(get_settings().database_path) as c:
        row = c.execute("SELECT run_id FROM triage_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)).fetchone()
    return row[0] if row else None


def triage(client, token: str, case_id: str, expected=AUTO, **overrides):
    """POST case triage. `expected` is the `expected_run_id` token (docs/17 §3b): by default the current latest run
    (a deliberate, up-to-date re-run, as the UI sends it); pass a run id, "none", or None (omit) to test the check."""
    if expected is AUTO:
        expected = latest_run_id(case_id) or "none"
    params = {} if expected is None else {"expected_run_id": expected}
    return client.post(f"/api/v1/cases/{case_id}/triage", json=triage_case(**overrides), headers=auth(token), params=params)


def audit_rows(client) -> list[dict]:
    import sqlite3

    from app.config import get_settings

    with sqlite3.connect(get_settings().database_path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY seq")]
