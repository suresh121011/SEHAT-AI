"""Quality check (architecture §10 stage 1) on synthetic fixtures and degraded copies of them."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageEnhance, ImageFilter

from app.ocr.quality import assess

IMG = Image.open(Path(__file__).parents[1] / "fixtures" / "ocr" / "cbc_low_platelet.png").convert("RGB")


@pytest.mark.parametrize("name,image,ok,reason", [
    ("clean", IMG, True, None),
    ("mild_blur", IMG.filter(ImageFilter.GaussianBlur(1.5)), True, None),
    ("heavy_blur", IMG.filter(ImageFilter.GaussianBlur(3)), False, "blurry"),
    ("small_tilt", IMG.rotate(4, expand=True, fillcolor="white"), True, None),
    ("big_tilt", IMG.rotate(12, expand=True, fillcolor="white"), False, "skewed"),
    ("dark", ImageEnhance.Brightness(IMG).enhance(0.2), False, "too_dark"),
    ("low_contrast", ImageEnhance.Contrast(IMG).enhance(0.1), False, "low_contrast"),
    ("blank", Image.new("RGB", IMG.size, "white"), False, "blank_page"),
])
def test_quality(name, image, ok, reason):
    q = assess(np.array(image))
    assert q.ok is ok, (name, q)
    if reason:
        assert reason in q.reasons


def test_skew_angle_is_measured_for_deskew():
    q = assess(np.array(IMG.rotate(4, expand=True, fillcolor="white")))
    assert q.skew_degrees is not None and 3.0 < abs(q.skew_degrees) < 5.0
