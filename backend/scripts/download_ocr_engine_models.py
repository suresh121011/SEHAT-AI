"""Explicit, one-time install of the Surya OCR 2 and Chandra OCR 2 models (docs/14 §4). Never run
automatically. Run with the OCR-engine environment (.venv-ocr), from the repo root:

    .venv-ocr/bin/python backend/scripts/download_ocr_engine_models.py

- Surya OCR 2: the official llama.cpp build `datalab-to/surya-ocr-2-gguf` at a pinned revision.
- Chandra OCR 2: the official `datalab-to/chandra-ocr-2` weights at a pinned revision (~10.6 GB BF16),
  SHA-256 checked against the pinned LFS hash, then quantized LOCALLY to 8-bit with MLX (~4.8 GB). This
  is post-training quantization, not the quantization-aware training (QAT) the architecture describes.
Everything lands in models/ocr (git-ignored). Only this script uses the network; the worker runs offline.
Both model licences are modified OpenRAIL-M (free for research / small startups) — check before any
commercial use.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "models" / "ocr"

SURYA_REPO, SURYA_REV = "datalab-to/surya-ocr-2-gguf", "6a3a4c30e5e74446d4f8b6afd05b2f2da970f470"
CHANDRA_REPO, CHANDRA_REV = "datalab-to/chandra-ocr-2", "af93b47dba1b47b6640c86ccf487ed2260ab9a09"
CHANDRA_WEIGHTS_SHA256 = "0804568be9f099d6479fad9ed77a4da4611f3c1e7bc6e009af7dce45e8aa3847"  # model.safetensors (HF LFS)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    from huggingface_hub import hf_hub_download, snapshot_download

    os.environ["HF_HOME"] = str(MODELS / "hf-home")
    for name in ("surya-2.gguf", "surya-2-mmproj.gguf"):
        p = hf_hub_download(SURYA_REPO, name, revision=SURYA_REV)
        print(f"Surya: {name} → {p}")

    bf16 = MODELS / "chandra-ocr-2-bf16"
    snapshot_download(CHANDRA_REPO, revision=CHANDRA_REV, local_dir=str(bf16))
    if _sha256(bf16 / "model.safetensors") != CHANDRA_WEIGHTS_SHA256:
        print("Chandra weights SHA-256 mismatch; refusing to convert", file=sys.stderr)
        return 1
    out = MODELS / "chandra-ocr-2-mlx-8bit"
    if not (out / "model.safetensors").is_file():
        subprocess.run([sys.executable, "-m", "mlx_vlm", "convert", "--hf-path", str(bf16), "--mlx-path", str(out),
                        "-q", "--q-bits", "8", "--q-group-size", "64"], check=True, env={**os.environ, "HF_HUB_OFFLINE": "1"})
    write_chandra_manifest(out)
    print(f"Chandra: 8-bit MLX build in {out} (the {bf16.name} folder can be deleted to save ~10 GB)")
    return 0


def write_chandra_manifest(out: Path) -> None:
    """Record the hashes of the locally converted build; the worker refuses to load files that differ."""
    import json

    files = {name: _sha256(out / name) for name in ("model.safetensors", "config.json")}
    (out / "SEHAT_ENGINE_MANIFEST.json").write_text(json.dumps({
        "source": f"{CHANDRA_REPO}@{CHANDRA_REV}", "source_weights_sha256": CHANDRA_WEIGHTS_SHA256,
        "conversion": "mlx_vlm convert -q --q-bits 8 --q-group-size 64 (post-training quantization, not QAT)",
        "files": files}, indent=1) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
