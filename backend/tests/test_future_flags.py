"""MEDGEMMA_ENABLED was a placeholder flag until the medical image pipeline (docs/18). It stays off by default, "0"
means off, and switching it on with a cloud backend but without the cloud gates refuses to start."""

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_off_by_default_and_zero_means_off(monkeypatch):
    monkeypatch.delenv("MEDGEMMA_ENABLED", raising=False)
    s = get_settings()
    assert s.medgemma_enabled is False
    get_settings.cache_clear()
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")  # bool("0") would be True — must parse as off
    s = get_settings()
    assert s.medgemma_enabled is False


@pytest.mark.parametrize("name", ["MEDGEMMA_ENABLED"])
def test_enabling_without_its_gates_refuses_to_start(monkeypatch, name):
    monkeypatch.setenv(name, "1")
    monkeypatch.setenv("OCR_ENABLED", "1")
    monkeypatch.setenv("OCR_RETENTION_DAYS", "none")
    monkeypatch.setenv("MEDGEMMA_BACKEND", "google_ai")
    monkeypatch.setenv("AI_CLOUD_ENABLED", "0")
    with pytest.raises(RuntimeError, match="AI_CLOUD_ENABLED"):
        get_settings()
