"""Regression: RapidOCR keeps call arguments as instance state. A 2× crop re-read (detection off) must not
leave later full-page reads without detection — that crashed every upload after the first re-read
(found in the 2026-10-02 browser walkthrough)."""
import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.ocr import engine


class StickyEngine:
    """Mimics RapidOCR 3.9.2 update_params: any non-None argument persists on the instance."""

    def __init__(self):
        self.use_det, self.use_cls, self.use_rec, self.return_word_box = True, True, True, False

    def __call__(self, img, **kw):
        for k, v in kw.items():
            if v is not None:
                setattr(self, k, v)
        if not self.use_det:
            return SimpleNamespace(txts=("12.5",), boxes=None, scores=(0.9,), word_results=None)
        quad = np.array([[[10, 10], [60, 10], [60, 30], [10, 30]]], dtype=float)
        words = ((("12.5", 0.9, [[10, 10], [60, 10], [60, 30], [10, 30]]),),) if self.return_word_box else None
        return SimpleNamespace(txts=("12.5",), boxes=quad, scores=(0.9,), word_results=words)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (100, 50), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_full_page_read_after_crop_reread_keeps_detection(monkeypatch):
    fake = StickyEngine()
    monkeypatch.setattr(engine, "_load", lambda model_dir: fake)
    png, md = _png(), Path("unused")
    assert engine.reread_crop(png, (10, 10, 60, 30), 2.0, model_dir=md) == ("12.5", 0.9)
    page = engine.read_page(png, 0, model_dir=md)
    assert [ln.text for ln in page.lines] == ["12.5"] and page.lines[0].bbox == (10, 10, 61, 31)
    assert page.lines[0].words, "word boxes must still be requested after a re-read"


def test_text_without_boxes_is_an_engine_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(engine, "_load", lambda model_dir: lambda img, **kw: SimpleNamespace(txts=("x",), boxes=None, scores=(0.9,), word_results=None))
    with pytest.raises(engine.EngineError):
        engine.read_page(_png(), 0, model_dir=Path("unused"))


def test_internal_error_log_has_class_and_location_but_no_content(caplog):
    from app.ocr import service

    try:
        raise AttributeError("CANARY-Haemoglobin 11.2 Mr Zzyzx")
    except AttributeError as exc:
        with caplog.at_level("ERROR", logger="sehat.ocr"):
            service._log_internal(exc)
    assert "type=AttributeError" in caplog.text and "test_engine_state.py" not in caplog.text
    assert "CANARY" not in caplog.text and "Zzyzx" not in caplog.text and "11.2" not in caplog.text
