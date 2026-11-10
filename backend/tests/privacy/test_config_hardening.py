"""Startup validation of auth and CORS settings (final remediation plan S-2, S-4). Fails closed at startup instead of
failing later at login. Synthetic values only."""

import pytest

from app.config import get_settings


def _load(monkeypatch, **env):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 48)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        return get_settings()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("alg", ["none", "RS256", "ES256", "hs256 ", ""])
def test_only_hmac_jwt_algorithms_are_accepted(monkeypatch, alg):
    with pytest.raises(RuntimeError, match="JWT_ALGORITHM"):
        _load(monkeypatch, JWT_ALGORITHM=alg)


@pytest.mark.parametrize("alg", ["HS256", "HS384", "HS512"])
def test_hmac_algorithms_load(monkeypatch, alg):
    assert _load(monkeypatch, JWT_ALGORITHM=alg).jwt_algorithm == alg


def test_a_short_jwt_secret_is_refused_without_echoing_it(monkeypatch):
    with pytest.raises(RuntimeError, match="at least 32 bytes") as exc:
        _load(monkeypatch, JWT_SECRET_KEY="short-sentinel-0003")
    assert "short-sentinel-0003" not in str(exc.value)


def test_a_wildcard_cors_origin_is_refused(monkeypatch):
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        _load(monkeypatch, CORS_ALLOWED_ORIGINS="http://localhost:3000,*")


def test_cors_never_allows_credentials(client):
    """The browser talks to the Next.js same-origin proxy; the backend uses bearer tokens, never cookies."""
    r = client.options("/api/v1/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-credentials") != "true"


# ── F2 / owner decision D-A: facility isolation fails closed ─────────────────────────────────────────────────

def test_startup_refuses_when_facility_isolation_is_off_and_not_explicitly_allowed(monkeypatch):
    monkeypatch.delenv("ACCOUNT_FACILITIES", raising=False)
    monkeypatch.delenv("ALLOW_UNSCOPED_DEMO", raising=False)
    with pytest.raises(RuntimeError, match="ACCOUNT_FACILITIES"):
        _load(monkeypatch)


def test_an_explicit_unscoped_demo_opt_out_starts_with_isolation_off(monkeypatch):
    monkeypatch.delenv("ACCOUNT_FACILITIES", raising=False)
    s = _load(monkeypatch, ALLOW_UNSCOPED_DEMO="true")
    assert s.account_facilities is None


def test_a_facility_mapping_starts_without_the_opt_out(monkeypatch):
    monkeypatch.delenv("ALLOW_UNSCOPED_DEMO", raising=False)
    s = _load(monkeypatch, ACCOUNT_FACILITIES="anm_demo=PHC-KHURDA-01;mo_demo=PHC-KHURDA-01;supervisor_demo=*;patient_demo=PHC-KHURDA-01")
    assert s.account_facilities is not None
