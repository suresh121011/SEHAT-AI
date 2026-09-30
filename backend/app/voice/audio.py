"""Audio intake and validation (docs/12 §3). In memory only.

The route reads the raw request body with a hard byte cap (no multipart: Starlette spools file parts
over 1 MB to a temporary file on disk). The browser recorder always produces 16 kHz mono PCM16 WAV,
so the stdlib `wave` module is enough: no ffmpeg/PyAV decoding of untrusted containers.
"""

from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import dataclass

import numpy as np

from app.errors import ApiError

SAMPLE_RATE = 16_000
WAV_HEADER_ALLOWANCE = 4_096


def max_bytes(max_seconds: int) -> int:
    return max_seconds * SAMPLE_RATE * 2 + WAV_HEADER_ALLOWANCE


@dataclass(frozen=True)
class AudioClip:
    samples: np.ndarray  # float32 mono in [-1, 1], 16 kHz
    wav_bytes: bytes  # the validated WAV, for engines that take a file (never persisted)
    sha256: str

    @property
    def duration_s(self) -> float:
        return len(self.samples) / SAMPLE_RATE

    @property
    def duration_ms(self) -> int:
        return int(round(self.duration_s * 1000))


class AudioInvalid(ApiError):
    def __init__(self, reason: str):
        super().__init__(400, "AUDIO_INVALID", "Audio must be a 16 kHz mono 16-bit PCM WAV within the length limit", {"reason": reason})


def validate_wav(data: bytes, max_seconds: int) -> AudioClip:
    if not data:
        raise AudioInvalid("empty")
    if len(data) > max_bytes(max_seconds):
        raise AudioInvalid("too_large")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if w.getcomptype() != "NONE":
                raise AudioInvalid("compressed")
            raw = w.readframes(frames)
    except AudioInvalid:
        raise
    except (wave.Error, EOFError, ValueError):
        raise AudioInvalid("not_wav") from None
    if channels != 1:
        raise AudioInvalid("not_mono")
    if width != 2:
        raise AudioInvalid("not_16bit")
    if rate != SAMPLE_RATE:
        raise AudioInvalid("not_16khz")
    if frames == 0 or len(raw) < 2:
        raise AudioInvalid("empty")
    if frames > max_seconds * SAMPLE_RATE:
        raise AudioInvalid("too_long")
    samples = np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2").astype(np.float32) / 32768.0
    return AudioClip(samples=samples, wav_bytes=data, sha256=hashlib.sha256(data).hexdigest())


def to_wav(samples: np.ndarray) -> bytes:
    """Encode float samples as 16 kHz mono PCM16 WAV (tests and fixtures)."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()
