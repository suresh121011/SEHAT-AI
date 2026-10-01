"""Untrusted document decoding (docs/14 §4B). Synthetic inputs only."""

import io
import os
import struct
import tempfile
import zlib
from pathlib import Path

import pytest
from PIL import Image

from app.ocr import files
from app.ocr.files import DocumentInvalid, normalize

FIX = Path(__file__).parents[1] / "fixtures" / "ocr"
PNG = (FIX / "cbc_low_platelet.png").read_bytes()
PDF = (FIX / "two_page_scan.pdf").read_bytes()


def _png_header_only(w: int, h: int) -> bytes:
    """A PNG whose IHDR claims w×h pixels (a 'decompression bomb' header) with a tiny body."""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)  # noqa: E731
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")


def test_png_is_normalised_to_one_stored_page():
    media, pages = normalize(PNG)
    assert media == "image/png" and len(pages) == 1
    p = pages[0]
    assert (p.width, p.height) == (1240, 1754) and p.transform["scale"] == 1.0 and p.transform["rotation_applied_deg"] == 0.0
    assert Image.open(io.BytesIO(p.png)).size == (p.width, p.height)


def test_pdf_pages_rendered_in_subprocess():
    media, pages = normalize(PDF)
    assert media == "application/pdf" and [p.index for p in pages] == [0, 1]
    assert all(p.transform["source"] == "pdf" and abs(p.width - 1240) <= 2 for p in pages)


def test_jpeg_exif_orientation_applied_and_metadata_stripped():
    img = Image.open(io.BytesIO(PNG)).convert("RGB").rotate(90, expand=True)  # stored sideways
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 CW to display"
    exif[0x8825] = {2: (1.0, 2.0, 3.0)}  # GPS block, must not survive
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif, quality=92)
    media, pages = normalize(buf.getvalue())
    p = pages[0]
    assert media == "image/jpeg" and p.transform["exif_orientation"] == 6
    assert (p.width, p.height) == (1240, 1754)  # upright again
    stored = Image.open(io.BytesIO(p.png))
    assert not stored.getexif() and not stored.info.get("exif") and not getattr(stored, "text", {})


def test_small_skew_is_deskewed_and_recorded():
    tilted = Image.open(io.BytesIO(PNG)).convert("RGB").rotate(4, expand=True, fillcolor="white")
    buf = io.BytesIO()
    tilted.save(buf, format="PNG")
    p = normalize(buf.getvalue())[1][0]
    assert 3.0 < abs(p.transform["rotation_applied_deg"]) < 5.0
    from app.ocr.quality import assess
    import numpy as np

    after = assess(np.array(Image.open(io.BytesIO(p.png)).convert("RGB")))
    assert after.skew_degrees is not None and abs(after.skew_degrees) < 1.0


def test_large_image_is_downscaled_with_scale_recorded():
    big = Image.new("RGB", (4000, 3000), "white")
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    p = normalize(buf.getvalue())[1][0]
    assert max(p.width, p.height) <= files.MAX_LONG_SIDE and p.transform["scale"] == pytest.approx(0.75, abs=1e-3)


@pytest.mark.parametrize("data,reason", [
    (b"GIF89a....", "not_supported"),
    (b"", "empty"),
    (b"\x89PNG\r\n\x1a\n" + b"garbage" * 10, "corrupt"),
    (b"%PDF-1.7\n" + b"not really a pdf" * 20, "corrupt"),
])
def test_invalid_inputs_rejected_with_fixed_reasons(data, reason):
    with pytest.raises(DocumentInvalid) as exc:
        normalize(data)
    assert exc.value.reason == reason


def test_pixel_bomb_rejected_from_header_before_decode():
    with pytest.raises(DocumentInvalid) as exc:
        normalize(_png_header_only(20000, 20000))
    assert exc.value.reason == "too_many_pixels"


def test_too_many_pdf_pages():
    page = Image.new("L", (200, 200), "white")
    buf = io.BytesIO()
    page.save(buf, format="PDF", save_all=True, append_images=[page] * files.MAX_PAGES)
    with pytest.raises(DocumentInvalid) as exc:
        normalize(buf.getvalue())
    assert exc.value.reason == "too_many_pages"


def test_pdf_timeout_kills_child():
    import multiprocessing

    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(DocumentInvalid) as exc:
        normalize(PDF, pdf_timeout_s=0.001)
    assert exc.value.reason == "pdf_timeout"
    assert {p.pid for p in multiprocessing.active_children()} <= before


def test_no_temp_files_written():
    tmp = Path(tempfile.gettempdir())
    before = set(os.listdir(tmp))
    normalize(PNG)
    normalize(PDF)
    assert set(os.listdir(tmp)) - before == set()
