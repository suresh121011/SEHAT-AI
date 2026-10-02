"""Guards for pages the engines cannot read (2026-10-02, a 375-px discharge summary): text too small → retake;
a looping vision-model reading → never used. Synthetic data only."""

from pathlib import Path

import pytest

from app.ocr import quality
from app.ocr.service import _Rejected, chandra_degenerate, run_pipeline
from app.ocr.types import Line, PageOCR, Word

FIX = Path(__file__).parents[1] / "fixtures" / "ocr"


def test_text_size_threshold_matches_measured_pages():
    assert quality.text_size_reason([12] * 90) == "text_too_small"  # unreadable discharge summary (median 12 px)
    assert quality.text_size_reason([14] * 93) is None  # smallest page that read well (median 14 px)
    assert quality.text_size_reason([32] * 40) is None  # fixtures
    assert quality.text_size_reason([8, 9, 10]) is None  # too few lines to judge


def _block(text, y):
    return {"label": "Text", "bbox": [20, y, 200, y + 8], "html": f"<p>{text}</p>"}


def test_looping_reading_is_degenerate_and_a_normal_page_is_not():
    loop = [_block(t, 100 + 8 * i) for i, t in enumerate(["Admission Date :", "Discharge Date :", "Diagnosis :"] * 12)]
    assert chandra_degenerate(loop)
    one_label_repeated = [_block(f"Line {i}", i) for i in range(10)] + [_block("Diagnosis :", 200 + i) for i in range(8)]
    assert chandra_degenerate(one_label_repeated)
    normal = [_block(t, 50 * i) for i, t in enumerate(["VY Hospital", "Dr A", "Date : 9/5/25", "BP : 120/80", "Pain in left ear",
                                                       "O/E vesicles", "Chest", "Herpes zoster", "① Acyclovir 800mg 5 times a day", "× 7 DAYS",
                                                       "② Sofradex ointment BID", "7 DAYS."])]
    assert not chandra_degenerate(normal)
    assert not chandra_degenerate([_block("Diagnosis :", i) for i in range(5)])  # too few blocks to judge


class _Engines:
    def __init__(self, line_h=32, chandra=None):
        self.line_h, self.chandra, self.calls = line_h, chandra, []

    def paddle_page(self, png, i):
        self.calls.append("paddle")
        lines = [Line(f"l{k}", f"Serum Sodium:{130 + k} mmol/L", (40, 100 + 60 * k, 900, 100 + 60 * k + self.line_h), 0.9,
                      (Word("x", (40, 100 + 60 * k, 900, 100 + 60 * k + self.line_h), 0.9),)) for k in range(8)]
        return PageOCR(i, 1240, 1754, lines)

    def paddle_reread(self, png, bbox, zoom):
        return None

    def surya_page(self, png):
        self.calls.append("surya")
        return {"blocks": []}

    def chandra_page(self, png):
        self.calls.append("chandra")
        return {"blocks": self.chandra}


@pytest.fixture
def settings(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("OCR_RETENTION_DAYS", "none")
    monkeypatch.setenv("OCR_RXNORM_DB", "/nonexistent")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_small_text_is_rejected_for_retake_before_the_slow_engines(settings):
    eng = _Engines(line_h=11)
    with pytest.raises(_Rejected) as exc:
        run_pipeline((FIX / "cbc_low_platelet.png").read_bytes(), "discharge_summary", settings, eng)
    assert exc.value.reasons == ["text_too_small"] and exc.value.quality_json[0]["median_line_px"] == 11
    assert eng.calls == ["paddle"]  # Chandra / Surya never ran on it


LOOP = [_block(t, 100 + 8 * i) for i, t in enumerate(["Admission Date :", "Discharge Date :", "Diagnosis : Stomach Ulcer"] * 12)]


def test_looping_chandra_reading_is_dropped_on_a_discharge_summary(settings):
    out = run_pipeline((FIX / "cbc_low_platelet.png").read_bytes(), "discharge_summary", settings, _Engines(chandra=LOOP))
    assert out["engines"]["chandra"] == "failed:degenerate_output"
    assert not out["meds"] and not any(f.candidate.source_engine == "chandra-ocr-2" for f in out["lab"])


def test_looping_chandra_reading_fails_a_prescription(settings):
    from app.ocr.engine import EngineError

    with pytest.raises(EngineError) as exc:
        run_pipeline((FIX / "cbc_low_platelet.png").read_bytes(), "prescription", settings, _Engines(chandra=LOOP))
    assert exc.value.reason == "chandra_degenerate_output"
