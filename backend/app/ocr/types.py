"""Engine-neutral OCR structures (docs/14 §5B). Coordinates: integer pixels [x0, y0, x1, y1] on the stored
page PNG, origin top-left, x1/y1 exclusive, page_index 0-based."""

from __future__ import annotations

from dataclasses import dataclass, field

BBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class Word:
    text: str
    bbox: BBox
    score: float | None  # engine recognizer score; NOT a probability that the value is correct


@dataclass(frozen=True)
class Line:
    line_id: str
    text: str
    bbox: BBox
    score: float | None
    words: tuple[Word, ...] = ()
    engine: str = "paddleocr"


@dataclass(frozen=True)
class Cell:
    """A table cell or layout block from a structure-aware engine (Surya table, Chandra block)."""

    text: str
    bbox: BBox
    score: float | None
    engine: str
    row: int | None = None
    col: int | None = None


@dataclass
class PageOCR:
    page_index: int
    width: int
    height: int
    lines: list[Line] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)  # second engine blocks with geometry; may be empty
    table_rows: list = field(default_factory=list)  # second engine table rows (app.ocr.surya_parse.TableRow)


def bbox_union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def center(b: BBox) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def contains_point(b: BBox, p: tuple[float, float]) -> bool:
    return b[0] <= p[0] < b[2] and b[1] <= p[1] < b[3]


def overlap_area(a: BBox, b: BBox) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def valid_bbox(b: BBox, width: int, height: int) -> bool:
    return 0 <= b[0] < b[2] <= width and 0 <= b[1] < b[3] <= height
