"""Audio validation and real Silero VAD inference on synthetic fixtures (docs/12 §9)."""

import io
import wave
from pathlib import Path

import numpy as np
import pytest

from app.voice.audio import AudioInvalid, max_bytes, to_wav, validate_wav

FIXTURES = Path(__file__).parents[1] / "fixtures" / "voice"


def _wav(rate=16000, channels=1, width=2, seconds=1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(rate * seconds) * channels * width)
    return buf.getvalue()


@pytest.mark.parametrize(
    "data, reason",
    [
        (b"", "empty"),
        (b"RIFF....not a wav", "not_wav"),
        (_wav(rate=8000), "not_16khz"),
        (_wav(rate=44100), "not_16khz"),
        (_wav(channels=2), "not_mono"),
        (_wav(width=1), "not_16bit"),
        (_wav(seconds=0), "empty"),
        (_wav(seconds=31), "too_large"),
    ],
)
def test_invalid_audio_rejected_with_reason(data, reason):
    with pytest.raises(AudioInvalid) as exc:
        validate_wav(data, max_seconds=30)
    assert exc.value.details == {"reason": reason}


def test_too_long_by_frames_even_if_bytes_fit():
    with pytest.raises(AudioInvalid) as exc:
        validate_wav(_wav(seconds=6), max_seconds=5)
    assert exc.value.details["reason"] in ("too_long", "too_large")


def test_valid_clip_and_hash():
    clip = validate_wav(_wav(seconds=2), max_seconds=30)
    assert clip.duration_ms == 2000 and len(clip.sha256) == 64
    assert max_bytes(30) > 30 * 16000 * 2


# ── Real Silero VAD (requires requirements-voice.txt) ────────────────────────────────────────────

silero = pytest.importorskip("silero_vad")


def _clip(name):
    return validate_wav((FIXTURES / name).read_bytes(), max_seconds=30)


def test_vad_finds_speech_in_synthetic_english_and_hindi():
    from app.voice.vad import detect

    for name in ("en_fever_102.wav", "hi_fever_102.wav"):
        clip = _clip(name)
        segs = detect(clip)
        assert segs, name
        assert all(0 <= s.start_s < s.end_s <= clip.duration_s + 0.05 for s in segs)
        assert sum(s.end_s - s.start_s for s in segs) > 1.0


def test_vad_silence_and_low_noise_have_no_speech():
    from app.voice.vad import detect

    silence = validate_wav(to_wav(np.zeros(16000 * 3, dtype=np.float32)), 30)
    noise = validate_wav(to_wav(np.random.default_rng(0).normal(0, 0.003, 16000 * 3).astype(np.float32)), 30)
    assert detect(silence) == []
    assert detect(noise) == []


def test_vad_clipped_recording_keeps_partial_speech():
    """A recording cut off mid-utterance still yields the speech that was captured, ending at the cut."""
    from app.voice.vad import detect

    full = _clip("en_fever_102.wav")
    cut = validate_wav(to_wav(full.samples[: int(16000 * 2.0)]), 30)
    segs = detect(cut)
    assert segs and segs[-1].end_s <= 2.05


def test_local_model_code_integrity_is_checked_before_any_vendor_code_runs(tmp_path):
    """A modified model_onnx.py (or missing manifest) is refused before import — nothing executes."""
    import hashlib
    import json

    from app.voice import engines

    (tmp_path / "assets").mkdir()
    files = {"model_onnx.py": b"raise SystemExit('must never run')\n", "config.json": b"{}", "assets/preprocessor.ts": b"x"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    with pytest.raises(engines.EngineError) as exc:
        engines._verify_code_files(tmp_path)
    assert exc.value.reason == "local_model_manifest_missing"
    manifest = {"revision": engines.LOCAL_MODEL_REVISION, "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}}
    (tmp_path / "SEHAT_MANIFEST.json").write_text(json.dumps(manifest))
    engines._verify_code_files(tmp_path)  # matches: passes
    (tmp_path / "model_onnx.py").write_bytes(b"import os  # tampered\n")
    with pytest.raises(engines.EngineError) as exc:
        engines._verify_code_files(tmp_path)
    assert exc.value.reason == "local_model_integrity_failed"
