"""Placeholder flag for a later phase (MedGemma; MAKER voting is implemented in Phase 6, docs/16): off by default, "0" means off, and switching
one on refuses to start because the feature does not exist yet."""

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
def test_enabling_an_unimplemented_feature_refuses_to_start(monkeypatch, name):
    monkeypatch.setenv(name, "1")
    with pytest.raises(RuntimeError, match="not implemented"):
        get_settings()
