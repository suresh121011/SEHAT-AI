import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def ai_client(tmp_path, monkeypatch):
    """App with the deterministic fake provider (AI_PROVIDER=fake)."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AI_PROVIDER", "fake")
    for name in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.setenv(name, "")

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()
