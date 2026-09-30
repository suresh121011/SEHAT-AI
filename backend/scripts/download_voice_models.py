"""Explicit, one-time download of the local speech model (docs/12 §4.3). Never run automatically.

    # 1. accept the model terms (gated): https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual
    # 2. log in:  .venv/bin/hf auth login
    # 3. from backend/:  ../.venv/bin/python scripts/download_voice_models.py

Downloads the pinned revision into VOICE_LOCAL_MODEL_DIR (default ./models/voice, git-ignored) and
prints file count, total size and a SHA-256 manifest so a later check can detect changed files.
Only this script talks to Hugging Face; at runtime the model is loaded with the Hub forced offline.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.voice.engines import LOCAL_MODEL_ID, LOCAL_MODEL_REVISION  # noqa: E402


def main() -> int:
    from huggingface_hub import snapshot_download

    target = get_settings().voice_local_model_dir
    target.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {LOCAL_MODEL_ID}@{LOCAL_MODEL_REVISION} → {target}")
    snapshot_download(repo_id=LOCAL_MODEL_ID, revision=LOCAL_MODEL_REVISION, local_dir=str(target))
    manifest, total = {}, 0
    for path in sorted(p for p in target.rglob("*") if p.is_file() and ".cache" not in p.parts):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[str(path.relative_to(target))] = digest
        total += path.stat().st_size
    (target / "SEHAT_MANIFEST.json").write_text(json.dumps({"repo": LOCAL_MODEL_ID, "revision": LOCAL_MODEL_REVISION, "files": manifest}, indent=1))
    print(f"{len(manifest)} files, {total / 1e9:.2f} GB; manifest written to SEHAT_MANIFEST.json")
    print("Review the remote code before enabling: model_onnx.py (loaded with trust_remote_code).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
