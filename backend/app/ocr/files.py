"""Untrusted document decoding (docs/14 §4B): sniff → caps → decode → normalise → stored page PNGs.

- Content is identified by magic bytes, not by name or client content-type.
- Image dimensions are read from the header and capped BEFORE the pixels are decoded.
- PDFs are rendered by PDFium in a separate spawned process with a hard timeout (killed on expiry), so
  a hostile or huge PDF cannot hang or crash the API process.
- Output pages are re-encoded PNGs (EXIF/GPS/text metadata dropped). Every geometric change (EXIF
  orientation, downscale, deskew rotation, PDF render scale) is recorded in `transform`, and OCR runs on
  exactly these stored pixels, so boxes never need re-mapping.
- Everything is in memory; nothing is written to disk by this module.
"""

from __future__ import annotations

import hashlib
import io
import multiprocessing as mp
from dataclasses import dataclass, field
from typing import Literal

MediaType = Literal["image/png", "image/jpeg", "application/pdf"]

MAX_PAGES = 5
MAX_LONG_SIDE = 3000
MAX_PIXELS = 12_000_000  # per stored page
MAX_DECODE_PIXELS = 50_000_000  # refuse to decode anything larger (header check)
PDF_RENDER_DPI = 150
PDF_TIMEOUT_S = 20.0
DESKEW_MIN_DEG = 0.5
DESKEW_MAX_DEG = 7.0


class DocumentInvalid(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class StoredPage:
    index: int
    png: bytes
    width: int
    height: int
    sha256: str
    transform: dict = field(default_factory=dict)


def sniff(data: bytes) -> MediaType:
    if not data:
        raise DocumentInvalid("empty")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    raise DocumentInvalid("not_supported")


def _encode_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)  # no `pnginfo`: no text chunks, no EXIF
    return buf.getvalue()


def _fit(img, transform: dict):
    w, h = img.size
    scale = min(1.0, MAX_LONG_SIDE / max(w, h), (MAX_PIXELS / (w * h)) ** 0.5)
    if scale < 1.0:
        from PIL import Image

        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    transform["scale"] = round(scale, 6)
    return img


def _deskew(img, transform: dict):
    """Rotate small skews (0.5°–7°) so table rows are horizontal; larger skews are rejected by quality."""
    import numpy as np

    from app.ocr.quality import assess

    q = assess(np.asarray(img))
    transform["measured_skew_deg"] = q.skew_degrees
    if q.skew_degrees is not None and DESKEW_MIN_DEG <= abs(q.skew_degrees) <= DESKEW_MAX_DEG:
        img = img.rotate(q.skew_degrees, expand=True, fillcolor="white")  # PIL rotates counter-clockwise
        transform["rotation_applied_deg"] = q.skew_degrees
    else:
        transform["rotation_applied_deg"] = 0.0
    return img


def _finish(index: int, img, transform: dict) -> StoredPage:
    img = _deskew(img.convert("RGB"), transform)
    png = _encode_png(img)
    return StoredPage(index, png, img.width, img.height, hashlib.sha256(png).hexdigest(), transform)


def _image_pages(data: bytes, media: MediaType) -> list[StoredPage]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:  # reads the header only
            w, h = probe.size
            if w * h > MAX_DECODE_PIXELS or w <= 0 or h <= 0:
                raise DocumentInvalid("too_many_pixels")
            probe.verify()
        img = Image.open(io.BytesIO(data))
        exif_orientation = int(img.getexif().get(0x0112, 1)) if media == "image/jpeg" else 1
        img.load()
    except DocumentInvalid:
        raise
    except Image.DecompressionBombError:
        raise DocumentInvalid("too_many_pixels") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise DocumentInvalid("corrupt") from None
    transform = {"source": "image", "exif_orientation": exif_orientation}
    img = ImageOps.exif_transpose(img)
    img = _fit(img, transform)
    return [_finish(0, img, transform)]


def _render_pdf_child(data: bytes, dpi: int, max_pages: int, max_pixels: int, conn) -> None:
    """Runs in a spawned child. Sends ("ok", [(png, w, h)]) or ("err", reason)."""
    try:
        import pypdfium2 as pdfium

        try:
            doc = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            conn.send(("err", "encrypted_pdf" if "password" in str(exc).lower() else "corrupt"))
            return
        n = len(doc)
        if n == 0:
            conn.send(("err", "empty"))
            return
        if n > max_pages:
            conn.send(("err", "too_many_pages"))
            return
        out = []
        for i in range(n):
            page = doc[i]
            w_pt, h_pt = page.get_size()
            scale = dpi / 72
            if (w_pt * scale) * (h_pt * scale) > max_pixels:
                scale = (max_pixels / (w_pt * h_pt)) ** 0.5
            img = page.render(scale=scale).to_pil().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out.append((buf.getvalue(), img.width, img.height, round(scale, 6)))
        conn.send(("ok", out))
    except Exception:  # noqa: BLE001 - reported as a fixed code only
        conn.send(("err", "corrupt"))
    finally:
        conn.close()


def _pdf_pages(data: bytes, timeout_s: float) -> list[StoredPage]:
    from PIL import Image

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_render_pdf_child, args=(data, PDF_RENDER_DPI, MAX_PAGES, MAX_PIXELS, child), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout_s):
            raise DocumentInvalid("pdf_timeout")
        status, payload = parent.recv()
    except EOFError:
        raise DocumentInvalid("corrupt") from None
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()
    if status != "ok":
        raise DocumentInvalid(payload)
    pages = []
    for i, (png, w, h, scale) in enumerate(payload):
        img = Image.open(io.BytesIO(png))
        img.load()
        pages.append(_finish(i, img, {"source": "pdf", "pdf_render_scale": scale, "scale": 1.0}))
    return pages


def normalize(data: bytes, *, pdf_timeout_s: float = PDF_TIMEOUT_S) -> tuple[MediaType, list[StoredPage]]:
    if not data:
        raise DocumentInvalid("empty")
    media = sniff(data)
    pages = _pdf_pages(data, pdf_timeout_s) if media == "application/pdf" else _image_pages(data, media)
    return media, pages
