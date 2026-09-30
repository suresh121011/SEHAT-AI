"""Error-path hygiene: no synthetic identifiers in logs or responses (docs/11 §G)."""

import logging
import uuid

import pytest

from app.config import get_settings
from tests.conftest import login
from tests.rules.vignettes import case

SYNTHETIC_PHONE = "9876543210"  # synthetic test value, not a real number
SYNTHETIC_NAME = "Testname Syntheticperson"


def _all_logs(caplog) -> str:
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''} {r.stack_info or ''}" for r in caplog.records)


def test_unhandled_exception_returns_generic_500_and_logs_no_values(client, caplog):
    @client.app.get("/api/v1/_test/boom")
    async def _boom():
        raise ValueError(f"patient {SYNTHETIC_NAME} phone {SYNTHETIC_PHONE}")

    caplog.set_level(logging.DEBUG)
    resp = client.get("/api/v1/_test/boom")
    assert resp.status_code == 500
    body = resp.text
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    assert resp.headers["X-Request-ID"]
    logs = _all_logs(caplog)
    for value in (SYNTHETIC_PHONE, SYNTHETIC_NAME):
        assert value not in body
        assert value not in logs
    assert "unhandled_error type=ValueError" in logs


def test_triage_process_contract_unchanged(client):
    token = login(client, "mo_demo", "medical_officer")
    ok = client.post("/api/v1/triage/process", json=case(), headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and ok.json()["urgency"] == "GREEN"
    assert ok.headers["X-Request-ID"]
    bad = client.post("/api/v1/triage/process", json=case(vitals={"spo2": 150}), headers={"Authorization": f"Bearer {token}"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"
    assert bad.json()["error"]["request_id"]


def test_valid_uuid_request_id_is_echoed_and_non_uuid_is_replaced(client):
    rid = str(uuid.uuid4())
    assert client.get("/api/v1/health", headers={"X-Request-ID": rid}).headers["X-Request-ID"] == rid
    replaced = client.get("/api/v1/health", headers={"X-Request-ID": f"{SYNTHETIC_PHONE} {SYNTHETIC_NAME}"}).headers["X-Request-ID"]
    assert SYNTHETIC_PHONE not in replaced
    uuid.UUID(replaced)


def test_validation_errors_do_not_echo_client_keys_or_values(client):
    token = login(client, "mo_demo", "medical_officer")
    data = case()
    data[SYNTHETIC_NAME] = SYNTHETIC_PHONE  # extra key whose *name* is personal data
    data["vitals"]["spo2"] = SYNTHETIC_PHONE  # wrong type carrying an identifier
    resp = client.post("/api/v1/triage/process", json=data, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400
    assert SYNTHETIC_NAME not in resp.text and SYNTHETIC_PHONE not in resp.text
    fields = {e["field"] for e in resp.json()["error"]["details"]["errors"]}
    assert "<extra>" in fields


@pytest.mark.parametrize("environment", ["production", "staging", "demo"])
def test_demo_auth_refuses_to_start_outside_development_or_test(monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 48)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="demo authentication"):
            get_settings()
    finally:
        get_settings.cache_clear()
