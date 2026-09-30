from tests.conftest import login
from tests.rules.vignettes import case


def _post(client, token, body):
    return client.post("/api/v1/triage/process", json=body, headers={"Authorization": f"Bearer {token}"})


def test_mo_gets_red_with_explanation(client):
    token = login(client, "mo_demo", "medical_officer")
    resp = _post(client, token, case(vitals={"resp_rate": 30}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["urgency"] == "RED"
    assert body["triggered_rules"][0]["rule_id"] == "ATP_RED_RR"
    assert body["triggered_rules"][0]["evidence"]["resp_rate"]["value"] == 30


def test_anm_allowed_patient_forbidden_and_anonymous_unauthorized(client):
    assert _post(client, login(client, "anm_demo", "anm"), case()).json()["urgency"] == "GREEN"
    assert _post(client, login(client, "patient_demo", "patient"), case()).status_code == 403
    assert client.post("/api/v1/triage/process", json=case()).status_code == 401


def test_invalid_vitals_return_validation_envelope(client):
    token = login(client, "mo_demo", "medical_officer")
    resp = _post(client, token, case(vitals={"spo2": 150}))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
