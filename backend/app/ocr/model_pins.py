"""Pinned OCR model artifacts (docs/14 §4). Pure data: imported by the download script and the engine.

URLs and SHA-256 values are copied from rapidocr 3.9.2 `default_models.yaml` (ModelScope, tag v3.9.2).
The three primary models are also bundled inside the rapidocr 3.9.2 wheel; their bundled bytes were
checked against these hashes on 2026-10-01. The engine never downloads anything: it loads these files
from OCR_MODEL_DIR by explicit path and refuses to start if a hash does not match.
"""

RAPIDOCR_VERSION = "3.9.2"
MANIFEST_NAME = "SEHAT_OCR_MANIFEST.json"

_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx"

# role -> (file name, url, sha256)
MODELS: dict[str, tuple[str, str, str]] = {
    "det": ("PP-OCRv6_det_small.onnx", f"{_BASE}/PP-OCRv6/det/PP-OCRv6_det_small.onnx",
            "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    "rec": ("PP-OCRv6_rec_small.onnx", f"{_BASE}/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
            "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    "cls": ("ch_ppocr_mobile_v2.0_cls_mobile.onnx", f"{_BASE}/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
}
