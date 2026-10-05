"""Explicit, one-time install of the LOCAL MedGemma 1.5 image describer (docs/18 §4a). Never run automatically.

    # 1. accept the Health AI Developer Foundations terms on https://huggingface.co/google/medgemma-1.5-4b-it
    #    (the project owner's legal decision; 403 GatedRepoError until access is granted)
    # 2. log in:  .venv/bin/hf auth login
    # 3. the OCR worker venv must exist (.venv-ocr with mlx-vlm 0.7.4; docs/14 §9)
    # 4. from backend/:  ../.venv/bin/python scripts/download_medgemma_local.py

Steps, failing closed:
- downloads the OFFICIAL repo at a pinned revision (~8.6 GB BF16) — ungated third-party mirrors are not used;
- checks every LFS file against the SHA-256 pinned below (= what the Hub publishes for that revision) and refuses to
  convert on any mismatch;
- converts LOCALLY to MLX 8-bit with the OCR worker's mlx-vlm (post-training quantization, as for Chandra, docs/14);
- writes SEHAT_ENGINE_MANIFEST.json with the SHA-256 of EVERY file of the converted build. The worker re-checks it
  before loading and refuses unlisted weight shards.

The model card excludes ECG, so the backend never sends `ecg_strip` images to it. Weights stay under models/
(git-ignored). Only this script talks to Hugging Face; the worker runs offline with egress blocked.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = "google/medgemma-1.5-4b-it"
REVISION = "91850547d9f0b2fdd21aa7c5f4f3d1a8a52c243b"
# Official Hugging Face LFS SHA-256 for REVISION (checked 2026-10-05).
OFFICIAL_SHA256 = {
    "model-00001-of-00002.safetensors": "5e4c75b0ef1fb009caee567ab244f9e354e915fda748d1b76179cc453a39b4b5",
    "model-00002-of-00002.safetensors": "958e39df78c35ddb812fbcb8b5e7f46e07f1b153c1589f2d3f0b41c3b0748d30",
    "tokenizer.json": "7d4046bf0505a327dd5a0abbb427ecd4fc82f99c2ceaa170bc61ecde12809b0c",
    "tokenizer.model": "1299c11d7cf632ef3b4e11937501358ada021bbdf7c47638d13c0ee982f2e79c",
}
CONVERSION = "mlx_vlm convert -q --q-bits 8 --q-group-size 64 (post-training quantization, not QAT)"
REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "models" / "medgemma"
BF16 = BASE / "medgemma-1.5-4b-it-bf16"
OUT = BASE / "medgemma-1.5-4b-it-mlx-8bit"
OCR_PYTHON = REPO_ROOT / ".venv-ocr" / "bin" / "python"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(out: Path) -> dict:
    files = {p.name: _sha256(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "SEHAT_ENGINE_MANIFEST.json"}
    manifest = {"source": f"{REPO}@{REVISION}", "source_sha256": OFFICIAL_SHA256, "conversion": CONVERSION, "files": files}
    (out / "SEHAT_ENGINE_MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main() -> int:
    from huggingface_hub import snapshot_download

    if not OCR_PYTHON.is_file():
        print(f"OCR worker venv not found at {OCR_PYTHON} (docs/14 §9); it provides mlx-vlm", file=sys.stderr)
        return 2
    print(f"Downloading {REPO}@{REVISION} → {BF16}")
    snapshot_download(repo_id=REPO, revision=REVISION, local_dir=str(BF16))
    for name, sha in OFFICIAL_SHA256.items():
        got = _sha256(BF16 / name)
        if got != sha:
            print(f"{name}: SHA-256 mismatch ({got[:12]}… != {sha[:12]}…); refusing to convert", file=sys.stderr)
            return 1
    print(f"{len(OFFICIAL_SHA256)} LFS files match the official SHA-256")
    if OUT.exists():
        print(f"{OUT} exists; remove it to convert again", file=sys.stderr)
        return 1
    subprocess.run([str(OCR_PYTHON), "-m", "mlx_vlm", "convert", "--hf-path", str(BF16), "--mlx-path", str(OUT),
                    "-q", "--q-bits", "8", "--q-group-size", "64"], check=True, env={"HF_HUB_OFFLINE": "1", "PATH": "/usr/bin:/bin"})
    manifest = write_manifest(OUT)
    total = sum((OUT / n).stat().st_size for n in manifest["files"])
    print(f"Converted: {len(manifest['files'])} files, {total / 1e9:.2f} GB; manifest written. Set MEDGEMMA_BACKEND=local to use it.")
    print(f"The BF16 download in {BF16} is no longer needed by the app and can be deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
