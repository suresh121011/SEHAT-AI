import os

import pytest
from fastapi.testclient import TestClient

# Deterministic settings: tests see only the variables they set, never a developer's .env or shell exports. Opt-in
# live tests (SEHAT_LIVE_*, RUN_LIVE_*) keep both, because they need real keys and model paths.
_LIVE = any(k.startswith(("SEHAT_LIVE_", "RUN_LIVE_")) and os.environ[k] == "1" for k in os.environ)
_SETTINGS_PREFIXES = ("AI_", "AZURE_OPENAI_", "GOOGLE_AI_", "GUARDRAILS_", "JWT_", "LOCAL_LLM_", "MEDGEMMA_", "OCR_", "SARVAM_",
                      "TRANSLATION_", "VOICE_")
_SETTINGS_NAMES = ("ACCOUNT_FACILITIES", "CORS_ALLOWED_ORIGINS", "DATABASE_PATH", "ENVIRONMENT", "LOG_LEVEL")
if not _LIVE:
    os.environ["SEHAT_DOTENV"] = "0"
    for _k in [k for k in os.environ if k.startswith(_SETTINGS_PREFIXES) or k in _SETTINGS_NAMES]:
        del os.environ[_k]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("ENVIRONMENT", "test")
    for name in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.setenv(name, "")

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def login(client, username: str, role: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "role": role})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]
