"""Silero VAD (MIT, snakers4/silero-vad 6.2.3) — speech regions only (docs/12 §4.1).

VAD detects *whether and where* someone is speaking. It is not a transcriber, and its output is never
used as evidence that a transcript is correct. The model ships inside the pip package (no download).
It is imported lazily so a deployment with voice disabled never loads torch.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.voice.audio import SAMPLE_RATE, AudioClip

_lock = threading.Lock()
_model = None


@dataclass(frozen=True)
class Segment:
    start_s: float
    end_s: float


class VadUnavailable(RuntimeError):
    pass


def _load():
    global _model
    with _lock:
        if _model is None:
            try:
                import torch
                from silero_vad import load_silero_vad
            except ImportError as exc:  # optional dependency (requirements-voice.txt)
                raise VadUnavailable("silero_vad_not_installed") from exc
            torch.set_num_threads(1)
            _model = load_silero_vad(onnx=True)
        return _model


def detect(clip: AudioClip) -> list[Segment]:
    import torch
    from silero_vad import get_speech_timestamps

    model = _load()
    with _lock:  # the ONNX session keeps recurrent state; one caller at a time
        stamps = get_speech_timestamps(torch.from_numpy(clip.samples), model, sampling_rate=SAMPLE_RATE, return_seconds=True)
    return [Segment(float(s["start"]), float(s["end"])) for s in stamps]
