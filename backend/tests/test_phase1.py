"""Phase 1 definition-of-done checks (docs/08 P1, docs/09 §1)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import Depends

from app.auth import Principal, Role, require_roles
from app.config import get_settings
from app.database import TABLES
from tests.conftest import login


def test_health_reports_ok_db_and_unconfigured_llm(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm"] == "not_configured"
    assert resp.headers["X-Request-ID"]


def test_tables_auto_created(client):
    conn = sqlite3.connect(get_settings().database_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert set(TABLES) <= names


@pytest.mark.parametrize(
    "username,role",
    [("patient_demo", "patient"), ("anm_demo", "anm"), ("mo_demo", "medical_officer"), ("supervisor_demo", "supervisor")],
)
def test_demo_accounts_login_and_me(client, username, role):
    token = login(client, username, role)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == role
    assert resp.json()["username"] == username


def test_login_rejects_unknown_account_and_role_mismatch(client):
    for body in ({"username": "nobody", "role": "patient"}, {"username": "patient_demo", "role": "medical_officer"}):
        resp = client.post("/api/v1/auth/login", json=body)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_login_validation_uses_error_envelope(client):
    resp = client.post("/api/v1/auth/login", json={"username": "patient_demo", "role": "doctor"})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["request_id"]


def test_missing_invalid_and_expired_tokens_are_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401

    settings = get_settings()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode({"sub": "u", "role": "patient", "exp": past}, settings.jwt_secret_key, algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    forged = jwt.encode({"sub": "u", "role": "admin", "exp": past + timedelta(days=2)}, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_header_mismatch_is_403(client):
    token = login(client, "patient_demo", "patient")
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}", "X-SEHAT-Role": "medical_officer"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_require_roles_blocks_other_roles(client):
    @client.app.get("/api/v1/_test/mo-only")
    async def _mo_only(p: Principal = Depends(require_roles(Role.MEDICAL_OFFICER))):
        return {"ok": True}

    mo = login(client, "mo_demo", "medical_officer")
    patient = login(client, "patient_demo", "patient")
    assert client.get("/api/v1/_test/mo-only", headers={"Authorization": f"Bearer {mo}"}).status_code == 200
    assert client.get("/api/v1/_test/mo-only", headers={"Authorization": f"Bearer {patient}"}).status_code == 403


def test_kernel_builds_with_plugin_and_no_llm(client):
    kernel = client.app.state.kernel
    assert "sehat" in kernel.plugins
