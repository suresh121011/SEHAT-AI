"""Opt-in real-engine tests (docs/12 §9.2). Skipped by default; never run in CI.

    # local IndicConformer (model installed via scripts/download_voice_models.py):
    SEHAT_LIVE_LOCAL=1 ../.venv/bin/pytest -m live -s tests/voice/test_live.py -k local
    # real Sarvam (SARVAM_API_KEY in .env; costs a few paise; synthetic audio only):
    SEHAT_LIVE_SARVAM=1 ../.venv/bin/pytest -m live -s tests/voice/test_live.py -k cloud

Output is sanitised: status, latency, language, candidate fields and the transcript of *synthetic*
fixtures only (never real people). The API key is never printed.
"""

import os
import resource
import socket
import time
from pathlib import Path

import pytest

from app.config import get_settings
from app.voice import engines
from app.voice.audio import validate_wav
from app.voice.extract import extract

pytestmark = pytest.mark.live
FIXTURES = Path(__file__).parents[1] / "fixtures" / "voice"


def _clip(name):
    return validate_wav((FIXTURES / name).read_bytes(), 30)


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # bytes on macOS


@pytest.fixture
def no_internet(monkeypatch):
    """Any attempt to open an internet socket fails the test (AF_UNIX stays allowed)."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(f"network access attempted during local inference: {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network access attempted")))


local = pytest.mark.skipif(os.getenv("SEHAT_LIVE_LOCAL") != "1", reason="set SEHAT_LIVE_LOCAL=1 (model must be installed)")
cloud = pytest.mark.skipif(os.getenv("SEHAT_LIVE_SARVAM") != "1", reason="set SEHAT_LIVE_SARVAM=1 (needs SARVAM_API_KEY)")


def _local_files(lang):
    return sorted(FIXTURES.glob(f"{lang}_*.wav"))


@local
@pytest.mark.parametrize("lang", ["hi", "or"])
def test_local_indicconformer_real_inference_without_network(lang, no_internet):
    model_dir = get_settings().voice_local_model_dir
    assert engines.local_model_installed(model_dir), f"model not installed in {model_dir}"
    files = _local_files(lang)
    if not files:
        pytest.skip(f"no {lang} fixture")
    for f in files:
        t0 = time.perf_counter()
        result = engines.transcribe_local(_clip(f.name), lang, model_dir=model_dir)
        dt = time.perf_counter() - t0
        assert result.engine == "local" and result.text.strip()
        fields = [(c.field, c.raw_value, c.flags) for c in extract(result.text)]
        print(f"\n[local {lang}] {f.name}: {dt:.2f}s peak_rss≈{_rss_mb():.0f}MB\n  transcript: {result.text}\n  candidates: {fields}")


@local
def test_local_rejects_english_without_calling_anything(no_internet):
    with pytest.raises(engines.EngineError) as exc:
        engines.transcribe_local(_clip("en_fever_102.wav"), "en", model_dir=get_settings().voice_local_model_dir)
    assert exc.value.reason == "language_unsupported_by_engine"


@cloud
@pytest.mark.parametrize("lang", ["en", "hi", "or"])
def test_cloud_saaras_real(lang):
    settings = get_settings()
    assert settings.sarvam_configured, "SARVAM_API_KEY missing"
    files = sorted(FIXTURES.glob(f"{lang}_*.wav"))
    if not files:
        pytest.skip(f"no {lang} fixture")
    for f in files:
        t0 = time.perf_counter()
        result = engines.transcribe_cloud(_clip(f.name), lang, api_key=settings.sarvam_api_key, timeout_s=settings.sarvam_timeout_s)
        dt = time.perf_counter() - t0
        assert result.engine == "cloud" and result.text.strip()
        fields = [(c.field, c.raw_value, c.flags) for c in extract(result.text)]
        print(f"\n[cloud {lang}] {f.name}: {dt:.2f}s warnings={list(result.warnings)}\n  transcript: {result.text}\n  candidates: {fields}")


@cloud
@pytest.mark.parametrize("lang", ["en", "hi", "or"])
def test_bulbul_tts_real(lang):
    from app.voice import tts
    from app.voice.readback import readback_text

    settings = get_settings()
    text = readback_text("temp", 102.0, None, "f", {"temp_c": (102 - 32) * 5 / 9}, lang)
    t0 = time.perf_counter()
    audio = tts.synthesize(text, lang, api_key=settings.sarvam_api_key, timeout_s=settings.sarvam_timeout_s)
    print(f"\n[tts {lang}] {len(audio)} bytes in {time.perf_counter() - t0:.2f}s")
    assert audio.startswith(b"RIFF")
