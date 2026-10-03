"""Explicit, one-time download of the local translation model (docs/16 §8, Phase 6 P2). Never run automatically.

    # 1. accept the model terms (gated): https://huggingface.co/ai4bharat/indictrans2-indic-en-dist-200M
    # 2. log in:  .venv/bin/hf auth login
    # 3. install:  uv pip install --python .venv/bin/python -r backend/requirements-translation.txt
    # 4. from backend/:  ../.venv/bin/python scripts/download_translation_models.py

Downloads the pinned revision into TRANSLATION_MODEL_DIR (default ./models/translation, git-ignored) and writes
a SHA-256 manifest that the server checks at startup. Only this script talks to Hugging Face.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.translate import MODEL_ID, MODEL_REVISION  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    from huggingface_hub import snapshot_download

    target = get_settings().translation_model_dir
    target.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {MODEL_ID}@{MODEL_REVISION} → {target}")
    snapshot_download(repo_id=MODEL_ID, revision=MODEL_REVISION, local_dir=str(target), allow_patterns=["*.json", "*.py", "*.safetensors", "model.SRC", "model.TGT", "LICENSE", "README.md"])
    manifest, total = {}, 0
    for path in sorted(p for p in target.rglob("*") if p.is_file() and ".cache" not in p.parts and p.name != "SEHAT_MANIFEST.json"):
        manifest[str(path.relative_to(target))] = hashlib.sha256(path.read_bytes()).hexdigest()
        total += path.stat().st_size
    (target / "SEHAT_MANIFEST.json").write_text(json.dumps({"repo": MODEL_ID, "revision": MODEL_REVISION, "files": manifest}, indent=1))
    print(f"{len(manifest)} files, {total / 1e9:.2f} GB; manifest written to SEHAT_MANIFEST.json")
    print("Review the remote code before enabling: configuration_indictrans.py, modeling_indictrans.py, tokenization_indictrans.py (trust_remote_code).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
