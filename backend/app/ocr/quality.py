"""Document image quality check — architecture §10 stage 1 ("blur, skew, lighting" → "ask patient to retake").

Deterministic OpenCV measures on the stored page image. Thresholds were set on the synthetic fixtures
and degraded copies of them (tests/ocr/test_quality.py); they are heuristics for "worth reading", not a
guarantee that OCR will be right on a page that passes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BLUR_MIN_VARIANCE = 60.0  # Laplacian variance on a 1000-px-wide grey copy; sharp print scores in the hundreds+
SKEW_MAX_DEGREES = 7.0  # beyond this, row clustering by y-centre breaks down
DARK_MAX_MEAN = 60.0  # mean grey (0-255) of the page background estimate
BRIGHT_CLIPPED_MAX = 0.98  # fraction of pixels at 255 that suggests a washed-out photo with lost text
CONTRAST_MIN = 40.0  # p95 - p5 grey spread


@dataclass(frozen=True)
class Quality:
    ok: bool
    reasons: tuple[str, ...]
    blur_variance: float
    skew_degrees: float | None
    mean_grey: float
    contrast: float


def _skew(grey: np.ndarray) -> float | None:
    import cv2

    _, bw = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # join characters into text-line blobs, then take the median angle of wide blobs
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, grey.shape[1] // 60), 3))
    joined = cv2.dilate(bw, kernel, iterations=1)
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles = []
    for cnt in contours:
        (cx, cy), (w, h), a = cv2.minAreaRect(cnt)
        if max(w, h) < grey.shape[1] * 0.08 or min(w, h) < 4:
            continue
        if w < h:
            a = a - 90 if a > 0 else a + 90
        if a > 45:
            a -= 90
        if a < -45:
            a += 90
        angles.append(a)
    return float(np.median(angles)) if angles else None


# Text too small to read reliably. assess() works on a 1000-px-wide copy, so a low-resolution page looks sharp
# there; this check uses the PaddleOCR line boxes on the stored page instead. Calibrated 2026-10-02 on the
# images we have: two pages with a median line box of 12 px gave wrong digits (132 read as 152) and a
# vision-model hallucination; the smallest page that read well had 14 px. The margin is narrow — revisit with
# more samples. Line boxes include a few pixels of padding, so 13 px is roughly 9 px glyphs.
MIN_MEDIAN_LINE_PX = 13
MIN_LINES_FOR_TEXT_SIZE = 5


def text_size_reason(line_heights: list[int]) -> str | None:
    """'text_too_small' when the page's median OCR line is below MIN_MEDIAN_LINE_PX (needs ≥5 lines to judge)."""
    if len(line_heights) < MIN_LINES_FOR_TEXT_SIZE:
        return None
    return "text_too_small" if float(np.median(line_heights)) < MIN_MEDIAN_LINE_PX else None


def assess(rgb: np.ndarray) -> Quality:
    import cv2

    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    scale = 1000 / grey.shape[1]
    small = cv2.resize(grey, (1000, max(1, int(grey.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    background = float(np.median(small))
    ink = float(np.percentile(small, 0.5))
    contrast = background - ink  # printed text against the page; a document is mostly background
    ink_fraction = float((small < background - max(20.0, contrast / 2)).mean())
    # Stretch to full range before measuring sharpness, so a dark-but-sharp photo is not called blurry.
    stretched = np.clip((small.astype(np.float64) - ink) * (255.0 / max(contrast, 1.0)), 0, 255)
    blur = float(cv2.Laplacian(stretched, cv2.CV_64F).var())
    skew = _skew(small)
    reasons = []
    if contrast < 15 or ink_fraction < 0.0005:
        reasons.append("blank_page")
    else:
        if blur < BLUR_MIN_VARIANCE:
            reasons.append("blurry")
        if skew is not None and abs(skew) > SKEW_MAX_DEGREES:
            reasons.append("skewed")
        if background < DARK_MAX_MEAN:
            reasons.append("too_dark")
        if contrast < CONTRAST_MIN:
            reasons.append("low_contrast")
        if float((small >= 255).mean()) > BRIGHT_CLIPPED_MAX:
            reasons.append("overexposed")
    return Quality(not reasons, tuple(reasons), round(blur, 1), None if skew is None else round(skew, 2), round(background, 1), round(contrast, 1))
