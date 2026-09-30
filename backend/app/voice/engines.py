"""Speech-to-text engines (docs/12 §4). Selection is explicit — `local` or `cloud` — and there is
no fallback in either direction: a failed or unavailable engine returns an explicit error.

- local: AI4Bharat IndicConformer-600M multilingual (MIT, gated on Hugging Face). Runs on this
  machine; no provider call. Loaded only from `VOICE_LOCAL_MODEL_DIR` with the Hub forced offline;
  it is never downloaded at runtime (see backend/scripts/download_voice_models.py).
- cloud: Sarvam AI Saaras v4 (`POST https://api.sarvam.ai/speech-to-text`, header
  `api-subscription-key`). The host is a constant; the key is sent only in that header and never
  logged or echoed. Errors become fixed reason codes; provider message text is discarded.

Neither engine provides a calibrated confidence score, so none is reported.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from app.voice.audio import AudioClip

Engine = Literal["local", "cloud"]
Language = Literal["en", "hi", "or"]

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_STT_MODEL = "saaras:v4"
SARVAM_LANGUAGE = {"en": "en-IN", "hi": "hi-IN", "or": "od-IN"}  # Sarvam uses od-IN for Odia

LOCAL_MODEL_ID = "ai4bharat/indic-conformer-600m-multilingual"
LOCAL_MODEL_REVISION = "e9b71b369c048e2c6b634d4c131061c34e441179"  # pinned; model_onnx.py reviewed at this commit (docs/12 §4)
LOCAL_LANGUAGE = {"en": None, "hi": "hi", "or": "or"}  # IndicConformer covers the 22 scheduled languages, not English
LOCAL_DECODER = "ctc"
LOCAL_BUSY_WAIT_S = 30  # inference itself cannot be interrupted once started (ONNX Runtime)


@dataclass(frozen=True)
class SttResult:
    text: str
    engine: Engine
    model_id: str
    mode: str
    language: Language
    warnings: tuple[str, ...] = field(default_factory=tuple)


class EngineError(Exception):
    """An engine could not produce a transcript. `reason` is a fixed code, safe to log and audit."""

    def __init__(self, reason: str, status: int = 503):
        super().__init__(reason)
        self.reason = reason
        self.status = status


# ── cloud: Sarvam Saaras v4 ──────────────────────────────────────────────────────────────────────


def transcribe_cloud(clip: AudioClip, language: Language, *, api_key: str, timeout_s: float, transport: httpx.BaseTransport | None = None) -> SttResult:
    if not api_key:
        raise EngineError("cloud_not_configured")
    try:
        with httpx.Client(timeout=timeout_s, transport=transport, follow_redirects=False) as client:
            resp = client.post(
                SARVAM_STT_URL,
                headers={"api-subscription-key": api_key},
                files={"file": ("audio.wav", clip.wav_bytes, "audio/wav")},
                data={"model": SARVAM_STT_MODEL, "mode": "transcribe", "language_code": SARVAM_LANGUAGE[language]},
            )
    except httpx.TimeoutException:
        raise EngineError("cloud_timeout", 504) from None
    except httpx.HTTPError:
        raise EngineError("cloud_unreachable") from None
    if resp.status_code == 429:
        raise EngineError("cloud_rate_limited", 429)
    if resp.status_code in (401, 403):
        raise EngineError("cloud_auth_failed")
    if resp.status_code >= 400:
        raise EngineError("cloud_rejected" if resp.status_code < 500 else "cloud_unavailable")
    try:
        body = resp.json()
        text = body["transcript"]
        if not isinstance(text, str):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise EngineError("cloud_bad_response", 502) from None
    warnings: list[str] = []
    returned = body.get("language_code")
    if returned and returned != SARVAM_LANGUAGE[language]:
        warnings.append("provider_reported_different_language")  # reported, never used to switch
    return SttResult(text=text, engine="cloud", model_id=SARVAM_STT_MODEL, mode="transcribe", language=language, warnings=tuple(warnings))


# ── local: IndicConformer-600M ───────────────────────────────────────────────────────────────────

_local_lock = threading.Lock()
_local_model = None


def local_model_installed(model_dir: Path) -> bool:
    return (model_dir / "config.json").is_file() and (model_dir / "model_onnx.py").is_file()


def local_supports(language: Language) -> bool:
    return LOCAL_LANGUAGE[language] is not None


# Files that execute code when the model loads (Python module, TorchScript preprocessor) plus config.
# They must match the SHA-256 manifest written by scripts/download_voice_models.py.
_CODE_FILES = ("model_onnx.py", "config.json", "assets/preprocessor.ts")


def _verify_code_files(model_dir: Path) -> None:
    import hashlib
    import json

    manifest_path = model_dir / "SEHAT_MANIFEST.json"
    if not manifest_path.is_file():
        raise EngineError("local_model_manifest_missing")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("revision") != LOCAL_MODEL_REVISION:
        raise EngineError("local_model_revision_mismatch")
    for name in _CODE_FILES:
        path = model_dir / name
        expected = (manifest.get("files") or {}).get(name)
        if expected is None or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise EngineError("local_model_integrity_failed")


def _load_local(model_dir: Path):
    """Load the reviewed model class straight from the local directory and construct it from local
    files. `AutoModel.from_pretrained` is deliberately NOT used: the vendor's override ignores
    `local_files_only`/`revision` and calls `snapshot_download` (reviewed at the pinned revision)."""
    global _local_model
    with _local_lock:
        if _local_model is None:
            if not local_model_installed(model_dir):
                raise EngineError("local_model_not_installed")
            _verify_code_files(model_dir)
            os.environ["HF_HUB_OFFLINE"] = "1"  # defence in depth; nothing below should reach the Hub
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            try:
                import importlib.util

                spec = importlib.util.spec_from_file_location("sehat_indic_asr_model", model_dir / "model_onnx.py")
                module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                spec.loader.exec_module(module)  # type: ignore[union-attr]
            except ImportError:
                raise EngineError("local_runtime_not_installed") from None
            try:
                _local_model = module.IndicASRModel(module.IndicASRConfig(ts_folder=str(model_dir)))
                _local_model.eval()
            except Exception:  # noqa: BLE001 - reported as a fixed code; details may include paths
                raise EngineError("local_model_load_failed") from None
        return _local_model


def transcribe_local(clip: AudioClip, language: Language, *, model_dir: Path) -> SttResult:
    code = LOCAL_LANGUAGE[language]
    if code is None:
        raise EngineError("language_unsupported_by_engine", 422)
    model = _load_local(model_dir)
    import torch

    wav = torch.from_numpy(clip.samples).unsqueeze(0)  # (1, N) float32, 16 kHz — as the model card expects
    # One inference at a time (ONNX sessions + model memory). Waiting requests give up after
    # LOCAL_BUSY_WAIT_S with an explicit "busy" error rather than queueing without bound.
    if not _local_lock.acquire(timeout=LOCAL_BUSY_WAIT_S):
        raise EngineError("local_busy", 503)
    try:
        text = model(wav, code, LOCAL_DECODER)
    except Exception:  # noqa: BLE001
        raise EngineError("local_inference_failed", 500) from None
    finally:
        _local_lock.release()
    if isinstance(text, (list, tuple)):
        text = text[0] if text else ""
    if not isinstance(text, str):
        raise EngineError("local_bad_output", 500)
    return SttResult(text=text, engine="local", model_id=f"{LOCAL_MODEL_ID}@{LOCAL_MODEL_REVISION[:12]}", mode=LOCAL_DECODER, language=language)
