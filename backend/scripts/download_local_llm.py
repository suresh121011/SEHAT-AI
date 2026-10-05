"""Explicit, one-time download of a pinned local text model for AI_PROVIDER=local (docs/16 §2a). Never run
automatically. The models are not gated (Apache-2.0).

    # from backend/:
    ../.venv/bin/python scripts/download_local_llm.py qwen3-4b-instruct-2507-q4km
    ../.venv/bin/python scripts/download_local_llm.py --list

Downloads the pinned revision of one GGUF file into LOCAL_LLM_MODEL_DIR (default ./models/llm, git-ignored),
checks its SHA-256 against the pin in app/ai/local_models.py and records it in SEHAT_LLM_MANIFEST.json.
A mismatch deletes the file. Only this script talks to Hugging Face.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.local_models import MANIFEST, MODELS, sha256_file  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--list":
        for m in MODELS.values():
            print(f"{m.key:32} {m.size / 1e9:.2f} GB  {m.license}  gate={m.gate:10}  {m.repo}@{m.revision[:12]}")
        return 0 if argv else 2
    model = MODELS.get(argv[0])
    if model is None:
        print(f"unknown model {argv[0]!r}; use --list", file=sys.stderr)
        return 2
    from huggingface_hub import hf_hub_download

    from dotenv import load_dotenv

    from app.config import REPO_ROOT, _env, _path

    load_dotenv(REPO_ROOT / ".env")  # same LOCAL_LLM_MODEL_DIR as the app (only this key is read)
    target = _path(_env("LOCAL_LLM_MODEL_DIR") or "./models/llm")
    target.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {model.repo}@{model.revision}/{model.filename} ({model.size / 1e9:.2f} GB) → {target}")
    path = Path(hf_hub_download(model.repo, model.filename, revision=model.revision, local_dir=str(target)))
    digest = sha256_file(path)
    if digest != model.sha256:
        path.unlink(missing_ok=True)
        print("SHA-256 mismatch; file deleted", file=sys.stderr)
        return 1
    manifest_path = target / MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        manifest = {}
    manifest[model.key] = {"repo": model.repo, "revision": model.revision, "filename": model.filename, "sha256": digest}
    manifest_path.write_text(json.dumps(manifest, indent=1))
    print(f"Verified SHA-256 {digest[:16]}…; recorded in {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
