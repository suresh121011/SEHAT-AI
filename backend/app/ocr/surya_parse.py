"""Parse Surya OCR 2 full-page output (blocks with `html`, `bbox`, `confidence`) into table rows keyed by
column role (docs/14 §5). Pure stdlib; runs in the backend, not in the OCR worker.

Surya gives one box per *block* (the whole table) and one confidence per block — no per-cell geometry or
per-value score. So its table rows are used as an independent second reading matched by test name and
column header, while precise highlight geometry and word scores come from PaddleOCR.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from app.ocr.lexicon import HEADER_WORDS, analyte_key, norm_key


@dataclass(frozen=True)
class TableRow:
    cells: dict[str, str]  # role -> printed text (name, value, unit, range, flag)
    bbox: tuple[int, int, int, int]  # the table block's box (Surya gives no cell boxes)
    score: float | None  # Surya block confidence (mean token probability; not a correctness probability)
    engine: str = "surya-ocr-2"

    @property
    def analyte_key(self) -> str | None:
        return analyte_key(self.cells.get("name", ""))


class _Table(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _roles(header: list[str]) -> list[str | None]:
    out: list[str | None] = []
    for h in header:
        key = norm_key(h)
        out.append(next((role for role, labels in HEADER_WORDS.items() if key in labels), None))
    return out


def table_rows(blocks: list[dict], engine: str = "surya-ocr-2") -> list[TableRow]:
    rows: list[TableRow] = []
    for b in blocks:
        if (b.get("label") or "").lower() not in ("table",) or b.get("error") or not b.get("html"):
            continue
        p = _Table()
        p.feed(b["html"])
        if not p.rows:
            continue
        roles = _roles(p.rows[0])
        if "name" not in roles or "value" not in roles:
            continue  # not a result table we can read by column
        bbox = tuple(int(round(x)) for x in b["bbox"])  # type: ignore[assignment]
        for r in p.rows[1:]:
            cells = {role: text for role, text in zip(roles, r) if role}
            if cells.get("name") and _roles([cells["name"]])[0] is None:  # skip repeated header rows
                rows.append(TableRow(cells, bbox, b.get("confidence"), engine))  # type: ignore[arg-type]
    return rows
