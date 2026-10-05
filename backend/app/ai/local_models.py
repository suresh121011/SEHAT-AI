"""Pinned local text models for AI_PROVIDER=local (docs/16 §2a). One GGUF file per model, served by a
loopback-only llama-server. Only scripts/download_local_llm.py talks to Hugging Face. The download script, the
start script and the backend at startup (app.ai.providers) each check the file's SHA-256 against the pin; the backend
cannot verify which file the running server loaded (it checks the model alias only).

All three are general-purpose instruct models, not medical models. Choice between them was made by
scripts/measure_local_llm.py on synthetic cases (docs/16 §2a); none is clinically validated.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "SEHAT_LLM_MANIFEST.json"


@dataclass(frozen=True)
class LocalModel:
    key: str
    repo: str
    revision: str
    filename: str
    sha256: str
    size: int
    license: str
    gate: str  # result of scripts/measure_local_llm.py on this project's synthetic smoke set (docs/16 §2a.4)


MODELS = {m.key: m for m in (
    LocalModel("qwen3-4b-instruct-2507-q4km", "unsloth/Qwen3-4B-Instruct-2507-GGUF", "a06e946bb6b655725eafa393f4a9745d460374c9",
               "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597", 2497281120, "Apache-2.0", "passed"),
    LocalModel("gemma-4-e4b-it-q4_0", "ggml-org/gemma-4-E4B-it-GGUF", "b8093469224f83f5c38f691eb906c380e9e63114",
               "gemma-4-E4B-it-Q4_0.gguf", "a555b900214b477d8880e7832e0b8925e139b0159640036b09fe472b6f2097f2", 4590807392, "Apache-2.0", "failed"),
    LocalModel("gemma-4-e2b-it-q4_0", "ggml-org/gemma-4-E2B-it-GGUF", "b4243c156154b6dca9324415f8c7ccc098b4aed1",
               "gemma-4-E2B-it-Q4_0.gguf", "8e30dff3ac4c8434c49a7036fa15564bdbb6044e42bf04550bf1a096ad7e6a52", 2841481184, "Apache-2.0", "unmeasured"),
)}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def installed(model_dir: Path, key: str) -> bool:
    """True when the download script recorded this model as verified and the file is still there with its size.
    (The full SHA-256 was checked at download time; re-hashing gigabytes at every capability call is avoided.)"""
    model = MODELS.get(key)
    if model is None:
        return False
    try:
        record = json.loads((model_dir / MANIFEST).read_text()).get(key) or {}
    except (OSError, ValueError):
        return False
    path = model_dir / model.filename
    return (record.get("sha256") == model.sha256 and record.get("revision") == model.revision
            and path.is_file() and path.stat().st_size == model.size)


def verified(model_dir: Path, key: str) -> bool:
    """Full SHA-256 of the model file against the pin (about 1–2 s for 2.5 GB). Run once at backend startup."""
    model = MODELS.get(key)
    path = model_dir / model.filename if model else None
    return bool(model and path and path.is_file() and sha256_file(path) == model.sha256)
