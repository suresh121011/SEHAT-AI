"""Patient-code entry rate limiting (docs/security/post-remediation-hardening.md A4, M-D). Synthetic data only.

Limits are server-side, per principal and per network source, shared by every app instance on the same database,
independent of request ids, fail closed when the store breaks, and never reveal whether a code exists.
"""

import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.config import get_settings
from tests.privacy.helpers import auth, token_for
from tests.review.test_patient_registry import _case, _new

API = "/api/v1"
A = "PHC-KHURDA-01"


@pytest.fixture
def lim(client, monkeypatch):
    for k, v in {"RATE_CODE_PER_MINUTE_PRINCIPAL": "3", "RATE_CODE_PER_HOUR_PRINCIPAL": "100", "RATE_CODE_PER_MINUTE_SOURCE": "50",
                 "RATE_CODE_PER_HOUR_SOURCE": "100", "RATE_BLOCK_MINUTES": "5"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield client
    get_settings.cache_clear()


def _lookup(c, tok, cid, code="SP-0000-0000", **headers):
    return c.post(f"{API}/cases/{cid}/patient/lookup", json={"patient_code": code}, headers={**auth(tok), **headers})


def _visit(c, anm):
    cid = _case(c, anm)
    return cid, _new(c, anm, _case(c, anm)).json()["patient_code"]


def _rows(sql, *a):
    with sqlite3.connect(get_settings().database_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, a)]


def test_attempts_within_the_limit_work_and_the_next_is_refused_with_retry_after(lim):
    anm = token_for(lim, "anm")
    cid, real = _visit(lim, anm)
    assert _lookup(lim, anm, cid, real).status_code == 200
    assert _lookup(lim, anm, cid).status_code in (400, 404)
    assert _lookup(lim, anm, cid).status_code in (400, 404)
    r = _lookup(lim, anm, cid, real)  # 4th attempt this minute: refused even for a real code
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) > 0
    assert real not in r.text and "patient" not in r.json()["error"]["message"].lower()


def test_the_block_stays_for_its_duration_even_for_valid_codes(lim):
    anm = token_for(lim, "anm")
    cid, real = _visit(lim, anm)
    for _ in range(4):
        _lookup(lim, anm, cid)
    assert all(_lookup(lim, anm, cid, real).status_code == 429 for _ in range(3))


def test_the_block_ends_and_counting_restarts(lim):
    anm = token_for(lim, "anm")
    cid, real = _visit(lim, anm)
    for _ in range(4):
        _lookup(lim, anm, cid)
    with sqlite3.connect(get_settings().database_path) as db:  # test-only: move the clock past the window and block
        db.execute("UPDATE rate_limit_blocks SET blocked_until = '2000-01-01T00:00:00.000000+00:00'")
        db.execute("UPDATE rate_limit_counters SET window_start = '2000-01-01T00:00:00.000000+00:00'")
    assert _lookup(lim, anm, cid, real).status_code == 200


def test_rotating_request_ids_does_not_reset_the_limit(lim):
    anm = token_for(lim, "anm")
    cid, _ = _visit(lim, anm)
    codes = [_lookup(lim, anm, cid, **{"X-Request-ID": f"rid-{i}"}).status_code for i in range(5)]
    assert codes[-1] == 429 and codes.count(429) == 2


def test_principals_are_counted_separately_but_the_source_is_shared(lim, monkeypatch):
    anm, other = token_for(lim, "anm"), token_for(lim, "anm_other")
    cid, _ = _visit(lim, anm)
    for _ in range(4):
        _lookup(lim, anm, cid)
    assert _lookup(lim, anm, cid).status_code == 429
    assert _lookup(lim, other, cid).status_code != 429  # another account is not punished for this one
    monkeypatch.setenv("RATE_CODE_PER_MINUTE_SOURCE", "2")
    monkeypatch.setenv("RATE_CODE_PER_MINUTE_PRINCIPAL", "50")
    get_settings.cache_clear()
    fresh = token_for(lim, "mo")  # a third account from the same source address
    assert _lookup(lim, fresh, cid).status_code == 429  # the source limit is spent by everyone on this address


def test_concurrent_attempts_are_counted_exactly(lim):
    anm = token_for(lim, "anm")
    cid, _ = _visit(lim, anm)
    out = []
    threads = [threading.Thread(target=lambda: out.append(_lookup(lim, anm, cid).status_code)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out.count(429) == 7  # exactly 3 got through, whatever the interleaving


def test_limits_are_shared_by_every_instance_on_the_same_database(lim):
    from app.main import create_app

    anm = token_for(lim, "anm")
    cid, _ = _visit(lim, anm)
    with TestClient(create_app()) as other:
        for c in (lim, other, lim):
            _lookup(c, anm, cid)
        assert _lookup(other, anm, cid).status_code == 429


def test_linking_and_binding_spend_the_same_budget(lim):
    anm = token_for(lim, "anm")
    cid, _ = _visit(lim, anm)
    for _ in range(3):
        lim.post(f"{API}/cases/{cid}/patient/link", json={"patient_code": "SP-0000-0000", "confirm": True}, headers=auth(anm))
    assert _lookup(lim, anm, cid).status_code == 429
    pat = token_for(lim, "patient")
    for _ in range(3):
        lim.post(f"{API}/me/patient-record/bind", json={"binding_code": "SB-0000-0000"}, headers=auth(pat))
    assert lim.post(f"{API}/me/patient-record/bind", json={"binding_code": "SB-0000-0000"}, headers=auth(pat)).status_code == 429


def test_unknown_and_out_of_scope_codes_answer_identically_within_the_limit(lim, monkeypatch):
    monkeypatch.setenv("RATE_CODE_PER_MINUTE_PRINCIPAL", "50")
    get_settings.cache_clear()
    anm = token_for(lim, "anm")
    cid, _ = _visit(lim, anm)
    from app.patient_registry import new_code

    # Well-formed codes (valid check character) that belong to nobody all get the same 404. Out-of-scope codes look the
    # same too: tests/review/test_patient_registry.py::test_unknown_and_out_of_scope_codes_look_the_same.
    answers = {(r.status_code, r.json()["error"]["code"]) for r in (_lookup(lim, anm, cid, new_code()) for _ in range(5))}
    assert answers == {(404, "NOT_FOUND")}


def test_a_broken_store_refuses_instead_of_allowing(lim):
    class Broken:
        async def hit(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")

    anm = token_for(lim, "anm")
    cid, real = _visit(lim, anm)
    lim.app.state.rate_limit_store = Broken()
    try:
        r = _lookup(lim, anm, cid, real)
        assert r.status_code == 503 and r.json()["error"]["code"] == "RATE_LIMIT_UNAVAILABLE" and real not in r.text
    finally:
        del lim.app.state.rate_limit_store


def test_unauthenticated_attempts_never_reach_the_limiter_or_the_code(lim):
    r = lim.post(f"{API}/cases/00000000-0000-4000-8000-000000000000/patient/lookup", json={"patient_code": "SP-0000-0000"})
    assert r.status_code == 401 and _rows("SELECT COUNT(*) AS n FROM rate_limit_counters")[0]["n"] == 0


def test_a_block_is_an_audited_abuse_event_without_identifiers(lim):
    anm = token_for(lim, "anm")
    cid, real = _visit(lim, anm)
    for _ in range(5):
        _lookup(lim, anm, cid, real)
    rows = _rows("SELECT actor_role, outcome, details_json FROM audit_log WHERE action = 'rate_limited'")
    assert len(rows) == 1 and rows[0]["outcome"] == "denied"
    assert json.loads(rows[0]["details_json"]) == {"scope": "patient_code_entry", "key_kind": "principal", "blocked_minutes": 5}
    keys = json.dumps(_rows("SELECT * FROM rate_limit_counters") + _rows("SELECT * FROM rate_limit_blocks"))
    assert "testclient" not in keys and real not in keys


def test_forwarded_for_is_believed_only_from_a_trusted_proxy(lim, monkeypatch):
    from starlette.requests import Request

    def req(peer, xff):
        return Request({"type": "http", "client": (peer, 1), "headers": [(b"x-forwarded-for", xff.encode())]})

    assert ratelimit.client_source(req("203.0.113.9", "198.51.100.1")) == "203.0.113.9"  # untrusted peer: header ignored
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1")
    get_settings.cache_clear()
    assert ratelimit.client_source(req("10.0.0.1", "198.51.100.1, 10.0.0.1")) == "198.51.100.1"
    assert ratelimit.client_source(req("10.0.0.1", "6.6.6.6, 198.51.100.1")) == "198.51.100.1"  # spoofed left hop ignored
    monkeypatch.setenv("TRUSTED_PROXIES", "not-an-ip")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="TRUSTED_PROXIES"):
        get_settings()


def test_sign_in_attempts_are_limited_per_source_so_anonymous_callers_cannot_flood_the_audit_log(client, monkeypatch):
    monkeypatch.setenv("RATE_LOGIN_PER_MINUTE_SOURCE", "3")
    get_settings.cache_clear()
    try:
        codes = [client.post(f"{API}/auth/login", json={"username": "nobody_here", "role": "anm"}).status_code for _ in range(6)]
        assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429, 429]
        failed = _rows("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'auth_login_failed'")[0]["n"]
        assert failed == 3  # refused attempts write nothing more than the one block event
        assert _rows("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'rate_limited'")[0]["n"] == 1
        assert client.post(f"{API}/auth/login", json={"username": "anm_demo", "role": "anm"}).status_code == 429  # even valid ones, while blocked
    finally:
        get_settings.cache_clear()
