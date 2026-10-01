"""Explicit, one-time install of the local OCR models (docs/14 §4). Never run automatically.

    # from backend/:  ../.venv/bin/python scripts/download_ocr_models.py

For each pinned model (app/ocr/model_pins.py): if the installed rapidocr wheel already bundles a file
with the pinned SHA-256 it is copied; otherwise it is downloaded from the pinned ModelScope URL. Every
file is checked against its pinned SHA-256 before it is kept. Writes OCR_MODEL_DIR (default
./models/ocr, git-ignored) plus SEHAT_OCR_MANIFEST.json. Only this script uses the network; at runtime
the engine loads these files by explicit path and refuses mismatched hashes.
"""

import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import REPO_ROOT  # noqa: E402
from app.ocr.model_pins import MANIFEST_NAME, MODELS, RAPIDOCR_VERSION  # noqa: E402

MAX_MODEL_BYTES = 64 * 1024 * 1024  # each pinned file is < 25 MB; refuse anything far larger


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundled_dir() -> Path | None:
    try:
        import rapidocr
    except ImportError:
        return None
    return Path(rapidocr.__file__).parent / "models"


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - pinned https URL from model_pins
        declared = int(resp.headers.get("Content-Length") or 0)
        if declared > MAX_MODEL_BYTES:
            raise SystemExit(f"refusing {url}: declared size {declared} bytes")
        print(f"  downloading {declared / 1e6:.1f} MB from {url}")
        total = 0
        with dest.open("wb") as out:
            while chunk := resp.read(1 << 20):
                total += len(chunk)
                if total > MAX_MODEL_BYTES:
                    raise SystemExit(f"refusing {url}: more than {MAX_MODEL_BYTES} bytes")
                out.write(chunk)


def main() -> int:
    import os

    target = Path(os.environ.get("OCR_MODEL_DIR") or REPO_ROOT / "models" / "ocr")
    if not target.is_absolute():
        target = REPO_ROOT / target
    target.mkdir(parents=True, exist_ok=True)
    bundled = _bundled_dir()
    files: dict[str, str] = {}
    total = 0
    for role, (name, url, sha) in MODELS.items():
        dest = target / name
        if dest.is_file() and _sha256(dest) == sha:
            print(f"{role}: {name} already present")
        elif bundled is not None and (bundled / name).is_file() and _sha256(bundled / name) == sha:
            shutil.copyfile(bundled / name, dest)
            print(f"{role}: {name} copied from the rapidocr {RAPIDOCR_VERSION} wheel")
        else:
            tmp = dest.with_suffix(".part")
            _download(url, tmp)
            if _sha256(tmp) != sha:
                tmp.unlink()
                raise SystemExit(f"SHA-256 mismatch for {name}; file discarded")
            tmp.replace(dest)
            print(f"{role}: {name} downloaded and verified")
        files[role] = name
        total += dest.stat().st_size
    manifest = {"rapidocr": RAPIDOCR_VERSION, "files": {r: {"name": MODELS[r][0], "sha256": MODELS[r][2]} for r in files}}
    (target / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(files)} models, {total / 1e6:.1f} MB in {target}; manifest {MANIFEST_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
