import pytest
from fastapi.testclient import TestClient


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
