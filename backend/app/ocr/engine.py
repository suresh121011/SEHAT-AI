"""PaddleOCR (PP-OCRv6 det/rec via the RapidOCR ONNX runtime) — the in-process printed-text engine.

- Models are loaded by explicit path from OCR_MODEL_DIR and checked against the pinned SHA-256 manifest
  (app/ocr/model_pins.py) before loading. Nothing is downloaded at runtime: with explicit paths RapidOCR
  never enters its download branch, and the recognizer must carry its character list inside the ONNX
  file (else it would fetch one) — both are checked here.
- One inference at a time (module lock). Scores are recognizer outputs, not probabilities of correctness.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
from pathlib import Path

from app.ocr.model_pins import MANIFEST_NAME, MODELS, RAPIDOCR_VERSION
from app.ocr.types import Line, PageOCR, Word

ENGINE_ID = f"paddleocr-ppocrv6-small (rapidocr {RAPIDOCR_VERSION})"
_lock = threading.Lock()
_engine = None
_engine_dir: Path | None = None


class EngineError(Exception):
    def __init__(self, reason: str, status: int = 503):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def model_installed(model_dir: Path) -> bool:
    return (model_dir / MANIFEST_NAME).is_file() and all((model_dir / MODELS[r][0]).is_file() for r in ("det", "rec", "cls"))


def _verify(model_dir: Path) -> None:
    if not model_installed(model_dir):
        raise EngineError("model_not_installed")
    manifest = json.loads((model_dir / MANIFEST_NAME).read_text())
    if manifest.get("rapidocr") != RAPIDOCR_VERSION:
        raise EngineError("model_integrity_failed")
    for role in ("det", "rec", "cls"):
        name, _, sha = MODELS[role]
        if hashlib.sha256((model_dir / name).read_bytes()).hexdigest() != sha:
            raise EngineError("model_integrity_failed")


def _load(model_dir: Path):
    global _engine, _engine_dir
    if _engine is not None and _engine_dir == model_dir:
        return _engine
    _verify(model_dir)
    try:
        import onnxruntime as ort
        from rapidocr import RapidOCR
    except ImportError:
        raise EngineError("runtime_not_installed") from None
    rec = model_dir / MODELS["rec"][0]
    if "character" not in ort.InferenceSession(str(rec)).get_modelmeta().custom_metadata_map:
        raise EngineError("model_integrity_failed")  # would otherwise download a dictionary
    for name in ("RapidOCR", "rapidocr"):
        logging.getLogger(name).setLevel(logging.WARNING)  # its INFO lines include file paths
    try:
        _engine = RapidOCR(params={
            "Det.model_path": str(model_dir / MODELS["det"][0]),
            "Rec.model_path": str(rec),
            "Cls.model_path": str(model_dir / MODELS["cls"][0]),
            "Global.log_level": "critical",
        })
    except Exception:  # noqa: BLE001 - fixed reason; details may include paths
        raise EngineError("model_load_failed") from None
    _engine_dir = model_dir
    return _engine


def _box(quad) -> tuple[int, int, int, int]:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return (int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1)


def read_page(png: bytes, page_index: int, *, model_dir: Path) -> PageOCR:
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    arr = np.asarray(img)
    with _lock:
        eng = _load(model_dir)
        try:
            # RapidOCR keeps every argument passed to __call__ as instance state (update_params), so each call
            # sets all step flags explicitly: a crop re-read must never leave detection switched off.
            res = eng(arr, use_det=True, use_cls=True, use_rec=True, return_word_box=True)
        except Exception:  # noqa: BLE001
            raise EngineError("inference_failed", 500) from None
    page = PageOCR(page_index, img.width, img.height)
    if res is None or res.txts is None:
        return page
    if res.boxes is None or res.scores is None:
        raise EngineError("inference_failed", 500)  # text without geometry is never used
    words_per_line = res.word_results or [()] * len(res.txts)
    for i, (text, quad, score, wr) in enumerate(zip(res.txts, res.boxes.tolist(), res.scores, words_per_line)):
        words = tuple(Word(w[0], _box(w[2]), float(w[1])) for w in (wr or ()) if w and w[0].strip())
        page.lines.append(Line(f"p{page_index}l{i}", text, _box(quad), float(score), words))
    return page


def reread_crop(png: bytes, bbox: tuple[int, int, int, int], zoom: float, *, model_dir: Path) -> tuple[str, float | None] | None:
    """Architecture §10 step 1: re-OCR one block at `zoom`× (recognition only, same engine family)."""
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    pad = max(4, (bbox[3] - bbox[1]) // 4)
    box = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(img.width, bbox[2] + pad), min(img.height, bbox[3] + pad))
    crop = img.crop(box)
    crop = crop.resize((max(1, int(crop.width * zoom)), max(1, int(crop.height * zoom))), Image.Resampling.LANCZOS)
    with _lock:
        eng = _load(model_dir)
        try:
            res = eng(np.asarray(crop), use_det=False, use_cls=False, use_rec=True, return_word_box=False)
        except Exception:  # noqa: BLE001
            return None
    if res is None or not res.txts:
        return None
    return (" ".join(res.txts).strip(), float(min(res.scores)) if res.scores else None)
