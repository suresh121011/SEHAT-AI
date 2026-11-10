"""Server-managed sessions and revocation (docs/security/post-remediation-hardening.md A2, M-A). Synthetic data only.

Revocation must stop the very next request, survive a restart, apply to every backend instance on the same database,
leave other users alone, and never put a token (or its hash) into a log or the audit log.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import sessions
from app.auth import Role, create_access_token, demo_user_id
from app.config import get_settings
from tests.privacy.helpers import auth, token_for

API = "/api/v1"


def _db():
    c = sqlite3.connect(get_settings().database_path)
    c.row_factory = sqlite3.Row
    return c


def _me(client, token):
    return client.get(f"{API}/auth/me", headers=auth(token))


def _login(client, username="anm_demo", role="anm"):
    r = client.post(f"{API}/auth/login", json={"username": username, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


def _new_app():
    from app.main import create_app

    return TestClient(create_app())


# ── creation ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_login_returns_an_opaque_token_and_stores_only_its_hash(client):
    body = _login(client)
    token = body["access_token"]
    assert token.startswith(sessions.TOKEN_PREFIX) and token.count(".") == 1  # not a JWT (header.payload.signature)
    with _db() as c:
        row = c.execute("SELECT * FROM sessions WHERE session_id = ?", (body["session_id"],)).fetchone()
        dump = json.dumps([dict(r) for r in c.execute("SELECT * FROM sessions")])
    assert row["token_sha256"] == sessions.token_hash(token) and token not in dump
    assert row["auth_mode"] == "demo" and row["revoked_at"] is None and row["idle_expires_at"] <= row["absolute_expires_at"]
    assert _me(client, token).json()["session_id"] == body["session_id"]


def test_malformed_unknown_and_jwt_shaped_tokens_are_401(client):
    for bad in ("x", sessions.TOKEN_PREFIX + "A" * 43, "eyJhbGciOiJIUzI1NiJ9.e30.sig", sessions.TOKEN_PREFIX + "A" * 42 + "!"):
        assert _me(client, bad).status_code == 401


# ── revocation ────────────────────────────────────────────────────────────────────────────────────────────────

def test_logout_revokes_the_session_immediately(client):
    token = _login(client)["access_token"]
    assert client.post(f"{API}/auth/logout", headers=auth(token)).json() == {"revoked": True}
    assert _me(client, token).status_code == 401
    assert client.post(f"{API}/auth/logout", headers=auth(token)).status_code == 401  # a revoked token cannot act


def test_revoking_one_session_leaves_the_users_other_sessions_and_other_users_alone(client):
    a1, a2 = _login(client), _login(client)
    mo = _login(client, "mo_demo", "medical_officer")
    r = client.post(f"{API}/auth/sessions/{a1['session_id']}/revoke", headers=auth(a2["access_token"]))
    assert r.status_code == 200
    assert _me(client, a1["access_token"]).status_code == 401
    assert _me(client, a2["access_token"]).status_code == 200 and _me(client, mo["access_token"]).status_code == 200


def test_another_users_session_id_looks_unknown_and_is_not_revoked(client):
    anm, mo = _login(client), _login(client, "mo_demo", "medical_officer")
    r = client.post(f"{API}/auth/sessions/{mo['session_id']}/revoke", headers=auth(anm["access_token"]))
    assert r.status_code == 404
    assert _me(client, mo["access_token"]).status_code == 200


def test_sign_out_everywhere_revokes_all_of_one_users_sessions_only(client):
    a = [_login(client) for _ in range(3)]
    mo = _login(client, "mo_demo", "medical_officer")
    listed = client.get(f"{API}/auth/sessions", headers=auth(a[0]["access_token"])).json()["sessions"]
    assert {s["session_id"] for s in listed} >= {x["session_id"] for x in a} and sum(s["current"] for s in listed) == 1
    assert client.post(f"{API}/auth/sessions/revoke-all", headers=auth(a[0]["access_token"])).json()["revoked_count"] >= 3
    assert all(_me(client, x["access_token"]).status_code == 401 for x in a)
    assert _me(client, mo["access_token"]).status_code == 200


def test_admin_can_revoke_a_users_sessions_and_nobody_else_can(client):
    mo = _login(client, "mo_demo", "medical_officer")
    admin = create_access_token("admin_test", Role.ADMIN)[0]
    target = demo_user_id("mo_demo")
    for who in ("anm", "mo", "supervisor"):
        assert client.post(f"{API}/auth/admin/users/{target}/sessions/revoke", headers=auth(token_for(client, who))).status_code == 403
    assert _me(client, mo["access_token"]).status_code == 200
    r = client.post(f"{API}/auth/admin/users/{target}/sessions/revoke", headers=auth(admin))
    assert r.status_code == 200 and r.json()["revoked_count"] >= 1
    assert _me(client, mo["access_token"]).status_code == 401
    assert _me(client, admin).status_code == 200


# ── durability and multiple instances ─────────────────────────────────────────────────────────────────────────

def test_revocation_survives_a_restart(client):
    keep, gone = _login(client), _login(client)
    client.post(f"{API}/auth/logout", headers=auth(gone["access_token"]))
    with _new_app() as restarted:  # a fresh process-equivalent on the same database file
        assert _me(restarted, gone["access_token"]).status_code == 401
        assert _me(restarted, keep["access_token"]).status_code == 200


def test_revocation_on_one_instance_applies_to_another(client):
    token = _login(client)["access_token"]
    with _new_app() as other:
        assert _me(other, token).status_code == 200
        assert client.post(f"{API}/auth/logout", headers=auth(token)).status_code == 200  # revoked via instance A
        assert _me(other, token).status_code == 401  # instance B refuses it on its next request


# ── expiry ────────────────────────────────────────────────────────────────────────────────────────────────────

def _set_times(session_id, **cols):
    with _db() as c:
        c.execute("DROP TRIGGER IF EXISTS sessions_immutable")  # test-only: move the clock by editing the row
        c.execute(f"UPDATE sessions SET {', '.join(f'{k} = ?' for k in cols)} WHERE session_id = ?", (*cols.values(), session_id))


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="microseconds")


def test_idle_and_absolute_expiry_end_a_session(client):
    idle, absolute = _login(client), _login(client)
    _set_times(idle["session_id"], idle_expires_at=_iso(-timedelta(seconds=1)))
    assert _me(client, idle["access_token"]).status_code == 401
    _set_times(absolute["session_id"], absolute_expires_at=_iso(-timedelta(seconds=1)), idle_expires_at=_iso(-timedelta(seconds=2)))
    assert _me(client, absolute["access_token"]).status_code == 401


def test_use_slides_the_idle_timeout_but_never_past_the_absolute_lifetime(client):
    s = _login(client)
    _set_times(s["session_id"], last_seen_at=_iso(-timedelta(minutes=5)), idle_expires_at=_iso(timedelta(minutes=1)),
               absolute_expires_at=_iso(timedelta(minutes=2)))
    assert _me(client, s["access_token"]).status_code == 200
    with _db() as c:
        row = c.execute("SELECT idle_expires_at, absolute_expires_at FROM sessions WHERE session_id = ?", (s["session_id"],)).fetchone()
    assert row["idle_expires_at"] == row["absolute_expires_at"]  # slid, and capped at the absolute lifetime


def test_patient_sessions_are_shorter_than_staff_sessions(client):
    p, a = _login(client, "patient_demo", "patient"), _login(client)
    assert p["expires_in"] == 2 * 3600 and a["expires_in"] == 12 * 3600


# ── integrity ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_revocation_cannot_be_undone_and_sessions_cannot_be_deleted(client):
    s = _login(client)
    client.post(f"{API}/auth/logout", headers=auth(s["access_token"]))
    with _db() as c:
        for sql in ("UPDATE sessions SET revoked_at = NULL, revoked_reason = NULL", "UPDATE sessions SET user_id = 'x'",
                    "UPDATE sessions SET absolute_expires_at = '9999'", "DELETE FROM sessions"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(sql)
    assert _me(client, s["access_token"]).status_code == 401


def test_concurrent_use_and_revocation_never_leave_a_live_revoked_session(client):
    """Requests sliding the idle timeout race a revocation; afterwards the session must be dead and stay dead."""
    s = _login(client)
    _set_times(s["session_id"], last_seen_at=_iso(-timedelta(minutes=5)))
    errors = []

    def use():
        try:
            for _ in range(5):
                _me(client, s["access_token"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=use) for _ in range(4)]
    for t in threads:
        t.start()
    client.post(f"{API}/auth/logout", headers=auth(s["access_token"]))
    for t in threads:
        t.join()
    assert not errors
    with _db() as c:
        row = c.execute("SELECT revoked_at FROM sessions WHERE session_id = ?", (s["session_id"],)).fetchone()
    assert row["revoked_at"] is not None and _me(client, s["access_token"]).status_code == 401


# ── audit and logs ────────────────────────────────────────────────────────────────────────────────────────────

def test_logins_and_revocations_are_audited_without_tokens(client, caplog):
    with caplog.at_level(logging.DEBUG):
        s = _login(client)
        client.post(f"{API}/auth/logout", headers=auth(s["access_token"]))
        assert client.post(f"{API}/auth/login", json={"username": "nobody_here", "role": "anm"}).status_code == 401
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT action, outcome, actor_role, details_json FROM audit_log WHERE action LIKE 'auth_%' OR action = 'session_revoked'")]
        token_hash = c.execute("SELECT token_sha256 FROM sessions WHERE session_id = ?", (s["session_id"],)).fetchone()[0]
    actions = [(r["action"], r["outcome"]) for r in rows]
    assert ("auth_login_succeeded", "success") in actions and ("session_revoked", "success") in actions
    assert ("auth_login_failed", "denied") in actions
    failed = next(r for r in rows if r["action"] == "auth_login_failed")
    assert failed["actor_role"] == "anonymous" and "nobody_here" not in failed["details_json"]
    blob = json.dumps(rows) + " ".join(r.getMessage() for r in caplog.records)
    assert s["access_token"] not in blob and token_hash not in blob


def test_demo_mode_reports_demo_accounts(client):
    assert client.get(f"{API}/auth/mode").json() == {"auth_mode": "demo", "demo_accounts": True}
