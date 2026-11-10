"""OIDC identity (docs/security/post-remediation-hardening.md A1, M-B). A local test issuer: keys are generated here and
served to the app through `app.state.oidc_jwks`, so no network and no real provider are involved. Synthetic data only.
"""

import json
import sqlite3
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.auth import Role, create_access_token
from app.config import get_settings
from tests.privacy.helpers import auth

API = "/api/v1"
ISS, AUD, A = "https://issuer.example.test", "sehat-web", "PHC-KHURDA-01"


def _keypair(kid):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": kid, "use": "sig", "alg": "RS256"}
    return key, jwk


KEY, JWK = _keypair("k1")
OTHER_KEY, _ = _keypair("k1")  # same kid, different key: a forged signature


def _oidc_env(monkeypatch, tmp_path, **extra):
    env = {"DATABASE_PATH": str(tmp_path / "oidc.db"), "JWT_SECRET_KEY": "x" * 48, "ENVIRONMENT": "test",
           "AUTH_MODE": "oidc", "OIDC_ISSUER": ISS, "OIDC_CLIENT_ID": AUD, "OIDC_JWKS_URL": f"{ISS}/jwks", **extra}
    monkeypatch.delenv("ALLOW_UNSCOPED_DEMO", raising=False)  # set by tests/conftest.py for demo-mode tests
    monkeypatch.delenv("ACCOUNT_FACILITIES", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture
def oc(tmp_path, monkeypatch):
    _oidc_env(monkeypatch, tmp_path)
    from app.main import create_app

    app = create_app()
    app.state.oidc_jwks = {"keys": [JWK]}
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


NONCE = "n0nce-abcdefghijklmnop"


def _token(key=KEY, kid="k1", alg="RS256", drop=(), **over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "sub": "subject-123", "iat": now, "exp": now + 300, "nonce": NONCE,
              "sehat_role": "medical_officer", "sehat_facilities": [A], **over}
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def _session(c, token, nonce=NONCE):
    return c.post(f"{API}/auth/oidc/session", json={"id_token": token, "nonce": nonce})


def _rows(sql, *a):
    with sqlite3.connect(get_settings().database_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, a)]


# ── valid identities ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_valid_staff_identity_becomes_a_scoped_server_session(oc):
    r = _session(oc, _token())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auth_mode"] == "oidc" and body["role"] == "medical_officer" and body["username"].startswith("oidc-")
    me = oc.get(f"{API}/auth/me", headers=auth(body["access_token"])).json()
    assert me["facility_scope"] == [A] and me["facility_isolation"] == "enforced" and me["auth_mode"] == "oidc"
    row = _rows("SELECT issuer, subject, auth_mode FROM sessions WHERE session_id = ?", body["session_id"])[0]
    assert row == {"issuer": ISS, "subject": "subject-123", "auth_mode": "oidc"}


def test_the_same_provider_identity_maps_to_the_same_user_and_different_subjects_do_not(oc):
    a1, a2 = _session(oc, _token()).json(), _session(oc, _token()).json()
    b = _session(oc, _token(sub="subject-456")).json()
    assert a1["user_id"] == a2["user_id"] != b["user_id"]


def test_a_patient_identity_ignores_any_facility_claim(oc):
    body = _session(oc, _token(sehat_role="patient", sehat_facilities=["*"])).json()
    assert body["role"] == "patient" and body["expires_in"] == 2 * 3600


def test_a_supervisor_may_carry_star_but_staff_may_not(oc):
    assert _session(oc, _token(sehat_role="supervisor", sehat_facilities="*")).status_code == 200
    assert _session(oc, _token(sehat_role="medical_officer", sehat_facilities="*")).status_code == 401


def test_an_oidc_session_is_revocable_like_any_other(oc):
    t = _session(oc, _token()).json()["access_token"]
    assert oc.post(f"{API}/auth/logout", headers=auth(t)).status_code == 200
    assert oc.get(f"{API}/auth/me", headers=auth(t)).status_code == 401


# ── rejected tokens ───────────────────────────────────────────────────────────────────────────────────────────

BAD = {
    "invalid_signature": lambda: _token(key=OTHER_KEY),
    "unknown_kid": lambda: _token(kid="k9"),
    "wrong_issuer": lambda: _token(iss="https://evil.example.test"),
    "wrong_audience": lambda: _token(aud="another-app"),
    "expired": lambda: _token(exp=int(time.time()) - 600, iat=int(time.time()) - 900),
    "not_yet_valid": lambda: _token(nbf=int(time.time()) + 600),
    "issued_in_the_future": lambda: _token(iat=int(time.time()) + 600),
    "missing_sub": lambda: _token(drop=("sub",)),
    "missing_exp": lambda: _token(drop=("exp",)),
    "missing_nonce": lambda: _token(drop=("nonce",)),
    "wrong_nonce": lambda: _token(nonce="a-different-nonce-value"),
    "alg_none": lambda: jwt.encode({"iss": ISS, "aud": AUD, "sub": "s", "exp": int(time.time()) + 300, "iat": int(time.time()),
                                    "nonce": NONCE, "sehat_role": "admin", "sehat_facilities": "*"}, None, algorithm="none"),
    "hs256_with_public_key_material": lambda: jwt.encode({"iss": ISS, "aud": AUD, "sub": "s", "exp": int(time.time()) + 300, "iat": int(time.time()),
                                                          "nonce": NONCE, "sehat_role": "admin"}, "k" * 64, algorithm="HS256", headers={"kid": "k1"}),
    "multiple_audiences_without_azp": lambda: _token(aud=[AUD, "another-app"]),
    "missing_role": lambda: _token(drop=("sehat_role",)),
    "unknown_role": lambda: _token(sehat_role="superuser"),
    "staff_without_facilities": lambda: _token(drop=("sehat_facilities",)),
    "malformed_facility": lambda: _token(sehat_facilities=["phc khurda; drop"]),
    "garbage": lambda: "not-a-jwt-at-all-just-text",
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_bad_identity_tokens_are_refused_with_one_generic_answer(oc, case):
    r = _session(oc, BAD[case]())
    assert r.status_code in (400, 401), (case, r.text)  # 400: not even a compact JWS (request validation)
    if r.status_code == 401:
        assert r.json()["error"]["message"] == "Sign-in failed"
    assert _rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"] == 0


def test_multiple_audiences_are_accepted_only_with_azp_equal_to_this_client(oc):
    assert _session(oc, _token(aud=[AUD, "another-app"], azp=AUD)).status_code == 200
    assert _session(oc, _token(aud=[AUD, "another-app"], azp="another-app")).status_code == 401


def test_refusals_are_audited_by_reason_only(oc):
    tok = _token(key=OTHER_KEY)
    _session(oc, tok)
    _session(oc, _token(sehat_role="superuser"))
    rows = _rows("SELECT outcome, actor_role, details_json FROM audit_log WHERE action = 'auth_login_failed'")
    reasons = [json.loads(r["details_json"])["reason"] for r in rows]
    assert reasons == ["invalid_token", "identity_not_allowed"] and {r["actor_role"] for r in rows} == {"anonymous"}
    blob = json.dumps(rows)
    assert tok not in blob and "subject-123" not in blob and "superuser" not in blob


# ── no demo in OIDC mode, and no OIDC in demo mode ────────────────────────────────────────────────────────────

def test_demo_login_is_switched_off_in_oidc_mode(oc):
    r = oc.post(f"{API}/auth/login", json={"username": "mo_demo", "role": "medical_officer"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "FEATURE_DISABLED"
    assert json.loads(_rows("SELECT details_json FROM audit_log WHERE action = 'auth_login_failed'")[-1]["details_json"])["reason"] == "auth_mode_disabled"


def test_a_demo_session_never_authorizes_in_oidc_mode(oc):
    demo = create_access_token("mo_demo", Role.MEDICAL_OFFICER)[0]  # e.g. minted before the switch
    assert oc.get(f"{API}/auth/me", headers=auth(demo)).status_code == 401


def test_the_oidc_endpoint_is_off_in_demo_mode(client):
    r = _session(client, _token())
    assert r.status_code == 404 and r.json()["error"]["code"] == "FEATURE_DISABLED"


# ── startup refuses unsafe or incomplete configuration ────────────────────────────────────────────────────────

@pytest.mark.parametrize("extra,match", [
    ({"OIDC_ISSUER": ""}, "OIDC_ISSUER"),
    ({"OIDC_CLIENT_ID": ""}, "OIDC_CLIENT_ID"),
    ({"OIDC_ALLOWED_ALGS": "HS256"}, "asymmetric"),
    ({"OIDC_ALLOWED_ALGS": "RS256,none"}, "asymmetric"),
    ({"OIDC_ISSUER": "http://issuer.example.test"}, "https"),
    ({"OIDC_JWKS_URL": "http://issuer.example.test/jwks"}, "https"),
    ({"ACCOUNT_FACILITIES": f"mo_demo={A}"}, "cannot be combined"),
    ({"ALLOW_UNSCOPED_DEMO": "true"}, "cannot be combined"),
    ({"AUTH_MODE": "both"}, "AUTH_MODE"),
    ({"OIDC_CLOCK_SKEW_S": "3600"}, "OIDC_CLOCK_SKEW_S"),
])
def test_startup_refuses_unsafe_or_incomplete_oidc_configuration(tmp_path, monkeypatch, extra, match):
    _oidc_env(monkeypatch, tmp_path, **extra)
    try:
        with pytest.raises(RuntimeError, match=match):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_a_loopback_http_issuer_is_allowed_for_a_local_test_provider(tmp_path, monkeypatch):
    _oidc_env(monkeypatch, tmp_path, OIDC_ISSUER="http://localhost:8080/realms/sehat", OIDC_JWKS_URL="")
    try:
        assert get_settings().oidc_issuer == "http://localhost:8080/realms/sehat"
    finally:
        get_settings.cache_clear()


def test_ids_are_never_emails_or_names(oc):
    body = _session(oc, _token(email="someone@example.test", name="Synthetic Person")).json()
    blob = json.dumps(_rows("SELECT * FROM sessions")) + json.dumps(body)
    assert "someone@example.test" not in blob and "Synthetic Person" not in blob
    uuid.UUID(body["user_id"])


def test_the_login_page_is_told_which_single_sign_in_method_exists(oc):
    assert oc.get(f"{API}/auth/mode").json() == {"auth_mode": "oidc", "demo_accounts": False}
