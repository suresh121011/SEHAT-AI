"""SARVAM_BASE_URL may only point at an allowlisted HTTPS host (docs/12 §2, §8): it decides where the API
key, patient audio and read-back text are sent."""

import pytest

from app.config import get_settings


@pytest.fixture
def settings_with(monkeypatch):
    def load(url: str | None):
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("JWT_SECRET_KEY", "x" * 48)
        if url is None:
            monkeypatch.delenv("SARVAM_BASE_URL", raising=False)
        else:
            monkeypatch.setenv("SARVAM_BASE_URL", url)
        get_settings.cache_clear()
        try:
            return get_settings()
        finally:
            get_settings.cache_clear()

    return load


@pytest.mark.parametrize("url", [None, "https://api.sarvam.ai", "https://api.sarvam.ai/", "https://API.Sarvam.ai", "https://api.sarvam.ai:443"])
def test_allowed_urls_are_normalised(settings_with, url):
    assert settings_with(url).sarvam_base_url == "https://api.sarvam.ai"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.sarvam.ai",  # not https
        "https://evil.example.com",  # host not allowlisted
        "https://api.sarvam.ai.evil.example.com",  # suffix trick
        "https://evil.example.com/api.sarvam.ai",  # host in the path
        "https://api.sarvam.ai@evil.example.com",  # userinfo trick: real host is evil.example.com
        "https://user:pass@api.sarvam.ai",  # credentials in the URL
        "https://api.sarvam.ai:8443",  # other port
        "https://api.sarvam.ai/v2",  # extra path
        "https://api.sarvam.ai/?x=1",  # query
        "https://api.sarvam.ai/#f",  # fragment
        "https://api.sarvam.ai:notaport",  # malformed port
        "https://api.sarvam.ai\n.evil",  # control character
        "file:///etc/passwd",
        "api.sarvam.ai",  # no scheme
    ],
)
def test_other_urls_refuse_to_start_without_echoing_the_value(settings_with, url):
    with pytest.raises(RuntimeError, match="SARVAM_BASE_URL must be https") as exc:
        settings_with(url)
    assert "evil" not in str(exc.value) and "pass" not in str(exc.value)
