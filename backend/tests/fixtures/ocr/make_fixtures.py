"""Synthetic printed lab-report fixtures for the OCR pipeline (docs/14). NOT real patient data.

Every report is drawn with Pillow's embedded font (Aileron Regular, SIL OFL), so the images are
reproducible on any machine. While drawing, the generator records each field's role, text and pixel
box in a sibling `<name>.truth.json`. These are ground truth for geometry and value tests: they prove
pipeline behaviour on synthetic layouts, not OCR accuracy on real reports.

Coordinates: integer pixels [x0, y0, x1, y1] on the page image, origin top-left, x1/y1 exclusive,
page_index 0-based. Pages are A4 at 150 dpi (1240 x 1754).

Run:  ../.venv/bin/python tests/fixtures/ocr/make_fixtures.py      (from backend/)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
DPI = 150
PAGE_W, PAGE_H = 1240, 1754
FONT = ImageFont.load_default(size=26)
FONT_BOLD_SIZE = 30
HEADER_FONT = ImageFont.load_default(size=FONT_BOLD_SIZE)

# Column left edges: test name, result, unit, printed reference range, flag.
COLS = {"name": 90, "value": 520, "unit": 690, "range": 880, "flag": 1110}
ROW_H = 46

# Synthetic identifiers only (canaries for log/audit leak tests). Not a real person or number.
CANARY_NAME = "Zzyzx Canary-Testpatient"
CANARY_PHONE = "+91 90000 00042"

Row = tuple[str, str, str, str, str]  # name, result, unit, range, flag (any may be "")

CBC_NORMAL: list[Row] = [
    ("Haemoglobin", "13.8", "g/dL", "13.0 - 17.0", ""),
    ("Total Leucocyte Count", "7,200", "/cumm", "4,000 - 11,000", ""),
    ("Platelet Count", "2,45,000", "/cumm", "1,50,000 - 4,10,000", ""),
    ("RBC Count", "4.92", "mill/cumm", "4.5 - 5.5", ""),
    ("Haematocrit (PCV)", "42.1", "%", "40 - 50", ""),
    ("MCV", "85.6", "fL", "83 - 101", ""),
    ("ESR", "12", "mm/hr", "0 - 20", ""),
]

CBC_LOW_PLATELET: list[Row] = [
    ("Haemoglobin", "11.2", "g/dL", "12.0 - 15.0", "L"),
    ("Total Leucocyte Count", "3,400", "/cumm", "4,000 - 11,000", "L"),
    ("Platelet Count", "85,000", "/cumm", "1,50,000 - 4,10,000", "L"),
    ("RBC Count", "4.10", "mill/cumm", "3.8 - 4.8", ""),
    ("Haematocrit (PCV)", "34.5", "%", "36 - 46", "L"),
    ("MCV", "84.0", "fL", "83 - 101", ""),
]

RENAL: list[Row] = [
    ("Blood Urea", "28", "mg/dL", "15 - 40", ""),
    ("Serum Creatinine", "1.42", "mg/dL", "0.7 - 1.3", "H"),
    ("Uric Acid", "6.1", "mg/dL", "3.5 - 7.2", ""),
    ("Sodium", "138", "mmol/L", "135 - 145", ""),
    ("Potassium", "4.6", "mmol/L", "3.5 - 5.1", ""),
    ("Troponin I", "<0.01", "ng/mL", "< 0.04", ""),
    ("HBsAg", "Non-Reactive", "", "Non-Reactive", ""),
]


@dataclass
class Page:
    image: Image.Image
    fields: list[dict] = field(default_factory=list)
    drawn: list[dict] = field(default_factory=list)


_DRAWN: list[dict] = []  # every text item drawn on the current page (for engine-free replay tests)


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font=FONT) -> list[int]:
    draw.text(xy, text, fill=(20, 20, 20), font=font)
    x0, y0, x1, y1 = draw.textbbox(xy, text, font=font)
    box = [int(x0), int(y0), int(x1) + 1, int(y1) + 1]  # exclusive right/bottom
    _DRAWN.append({"text": text, "bbox": box})
    return box


def _page(title: str, rows: list[Row], *, page_no: int, pages: int, report_date: str, collected: str) -> Page:
    _DRAWN.clear()
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(img)
    page = Page(img)

    def rec(role: str, text: str, bbox: list[int], row: int | None = None) -> None:
        page.fields.append({"role": role, "text": text, "bbox": bbox, "row": row})

    # Repeated header block (same on every page).
    rec("lab_name", "SEHAT Synthetic Diagnostics", _text(draw, (90, 60), "SEHAT Synthetic Diagnostics", HEADER_FONT))
    _text(draw, (90, 110), "SYNTHETIC TEST REPORT - NOT A REAL PATIENT")
    _text(draw, (90, 160), f"Patient: {CANARY_NAME}")
    _text(draw, (700, 160), f"Ph: {CANARY_PHONE}")
    _text(draw, (90, 200), "Collected:")
    rec("collected_date", collected, _text(draw, (260, 200), collected))
    _text(draw, (700, 200), "Reported:")
    rec("report_date", report_date, _text(draw, (860, 200), report_date))
    draw.line([(80, 250), (PAGE_W - 80, 250)], fill=(0, 0, 0), width=2)
    _text(draw, (90, 270), title, HEADER_FONT)

    # Column header.
    y = 330
    for key, label in (("name", "Test Name"), ("value", "Result"), ("unit", "Unit"), ("range", "Reference Range"), ("flag", "Flag")):
        _text(draw, (COLS[key], y), label)
    draw.line([(80, y + 38), (PAGE_W - 80, y + 38)], fill=(0, 0, 0), width=1)

    # Result rows.
    y += ROW_H + 10
    for i, row in enumerate(rows):
        for key, text in zip(("name", "value", "unit", "range", "flag"), row):
            if text:
                rec(key, text, _text(draw, (COLS[key], y), text), row=i)
        y += ROW_H

    # Repeated footer.
    _text(draw, (90, PAGE_H - 110), "End of report section. Values are synthetic.")
    _text(draw, (PAGE_W - 260, PAGE_H - 110), f"Page {page_no} of {pages}")
    page.drawn = list(_DRAWN)
    return page


def _truth(name: str, media: str, pages: list[Page]) -> dict:
    return {
        "fixture": name,
        "media_type": media,
        "synthetic": True,
        "coordinate_frame": "pixels on the page image, origin top-left, x1/y1 exclusive, page_index 0-based",
        "dpi": DPI,
        "pages": [{"page_index": i, "width": p.image.width, "height": p.image.height, "fields": p.fields, "drawn": p.drawn} for i, p in enumerate(pages)],
    }


def _save_png(name: str, page: Page) -> None:
    page.image.save(OUT / f"{name}.png", optimize=True)
    (OUT / f"{name}.truth.json").write_text(json.dumps(_truth(f"{name}.png", "image/png", [page]), indent=1) + "\n")


def _save_pdf(name: str, pages: list[Page]) -> None:
    """Scanned-style PDF: each page is an embedded image (no text layer)."""
    first, *rest = [p.image.convert("L") for p in pages]  # grayscale keeps the fixture small
    first.save(OUT / f"{name}.pdf", save_all=True, append_images=rest, resolution=DPI)
    (OUT / f"{name}.truth.json").write_text(json.dumps(_truth(f"{name}.pdf", "application/pdf", pages), indent=1) + "\n")


HAND_FONT_PATH = OUT / "fonts" / "Caveat-Variable.ttf"  # SIL OFL 1.1 (fonts/Caveat-OFL.txt), sha256 0bdb6b66…7988

RX_LINES = [
    "Rx",
    "1. Tab. Paracetamol 650 mg 1-0-1 x 5 days",
    "2. Cap. Amoxycillin 500 mg 1-1-1 x 7 days",
    "3. Tab. Pantoprazole 40 mg 1-0-0 before food",
    "4. Syp. Ambroxol 10 ml TDS",
    "Advice: plenty of oral fluids, review after 5 days",
]


def _handwritten_rx() -> Page:
    """Synthetic handwriting-style prescription (OFL font + seeded jitter). Not a real prescription."""
    import random

    rng = random.Random(20261001)
    hand = ImageFont.truetype(str(HAND_FONT_PATH), 46)
    _DRAWN.clear()
    img = Image.new("RGB", (PAGE_W, PAGE_H), (250, 249, 244))
    draw = ImageDraw.Draw(img)
    page = Page(img)
    _text(draw, (90, 60), "SEHAT Synthetic Clinic - SYNTHETIC PRESCRIPTION, NOT REAL", HEADER_FONT)
    _text(draw, (90, 120), f"Patient: {CANARY_NAME}    Date: 15/09/2026")
    draw.line([(80, 170), (PAGE_W - 80, 170)], fill=(0, 0, 0), width=2)
    y = 230
    for i, text in enumerate(RX_LINES):
        x = 110 + rng.randint(-8, 8)
        layer = Image.new("RGBA", (PAGE_W, 110), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((x, 20), text, fill=(25, 35, 110, 255), font=hand)
        angle = rng.uniform(-1.2, 1.2)
        layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, center=(x, 50))
        bbox = layer.getbbox()
        img.paste(layer, (0, y - 20), layer)
        box = [bbox[0], y - 20 + bbox[1], bbox[2], y - 20 + bbox[3]]
        page.fields.append({"role": "rx_line", "text": text, "bbox": box, "row": i})
        _DRAWN.append({"text": text, "bbox": box})
        y += 95 + rng.randint(-6, 6)
    page.drawn = list(_DRAWN)
    return page


def main() -> None:
    _save_png("cbc_normal", _page("Complete Blood Count", CBC_NORMAL, page_no=1, pages=1, report_date="14/09/2026", collected="14/09/2026"))
    _save_png("cbc_low_platelet", _page("Complete Blood Count", CBC_LOW_PLATELET, page_no=1, pages=1, report_date="15/09/2026", collected="15/09/2026"))
    _save_png("rx_handwritten", _handwritten_rx())
    _save_pdf("two_page_scan", [
        _page("Complete Blood Count", CBC_LOW_PLATELET, page_no=1, pages=2, report_date="15/09/2026", collected="15/09/2026"),
        _page("Renal Function and Others", RENAL, page_no=2, pages=2, report_date="15/09/2026", collected="15/09/2026"),
    ])


if __name__ == "__main__":
    main()
