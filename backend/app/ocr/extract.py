"""Printed lab-report table extraction (docs/14 §5): OCR words → rows → typed candidates with one source
region per field role. Deterministic; it reads only what is printed and never fills a missing value.

Columns come from the report's own header row (Test/Result/Unit/Reference range/Flag). Without a header
the row is parsed by pattern and flagged `no_column_header`. Words are assigned to a column by their
left edge, so a value and a flag printed in one OCR line still land in different fields.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from app.ocr.lexicon import DATE_LABELS, HEADER_WORDS, analyte_key, norm_key, unit_key
from app.ocr.parse import ParsedRange, ParsedUnit, ParsedValue, parse_flag, parse_range, parse_unit, parse_value
from app.ocr.types import BBox, Line, PageOCR, Word, bbox_union, center

ROLES = ("name", "value", "unit", "range", "flag")
_DATE = re.compile(r"\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\b")


@dataclass(frozen=True)
class Region:
    role: str  # name | value | unit | range | flag | date
    page_index: int
    bbox: BBox
    line_id: str
    granularity: str  # word | line


@dataclass
class LabCandidate:
    page_index: int
    row_index: int
    name_raw: str
    analyte_key: str | None
    value: ParsedValue
    unit: ParsedUnit
    range: ParsedRange
    flag_raw: str
    printed_flag: str | None
    regions: list[Region]
    value_score: float | None  # min recognizer score over the value's words (None if the engine gives none)
    flags: list[str] = field(default_factory=list)
    source_engine: str = "paddleocr"  # the engine whose reading this candidate is; others are checked against it


@dataclass
class DocDates:
    collected_date: str | None = None
    report_date: str | None = None
    regions: list[Region] = field(default_factory=list)


@dataclass
class _Tok:
    text: str
    bbox: BBox
    score: float | None
    line_id: str
    granularity: str
    line_bbox: BBox | None = None
    line_words: int = 1


def _tokens(page: PageOCR) -> list[_Tok]:
    out: list[_Tok] = []
    for line in page.lines:
        if line.words:
            words = [w for w in line.words if w.text.strip()]
            out += [_Tok(w.text, w.bbox, w.score, line.line_id, "word", line.bbox, len(words)) for w in words]
        elif line.text.strip():
            out.append(_Tok(line.text, line.bbox, line.score, line.line_id, "line"))
    return out


def _rows(toks: list[_Tok]) -> list[list[_Tok]]:
    """Cluster tokens into visual rows by vertical-centre proximity."""
    if not toks:
        return []
    h = statistics.median(t.bbox[3] - t.bbox[1] for t in toks) or 1
    rows: list[list[_Tok]] = []
    for t in sorted(toks, key=lambda t: center(t.bbox)[1]):
        cy = center(t.bbox)[1]
        if rows and abs(cy - statistics.mean(center(x.bbox)[1] for x in rows[-1])) <= 0.5 * h:
            rows[-1].append(t)
        else:
            rows.append([t])
    return [sorted(r, key=lambda t: t.bbox[0]) for r in rows]


def _header_anchors(row: list[_Tok]) -> dict[str, int] | None:
    """Column left edges if this row is a table header (≥3 roles incl. name and value)."""
    anchors: dict[str, int] = {}
    i = 0
    while i < len(row):
        matched = False
        for span in (4, 3, 2, 1):  # multi-word labels like "Biological Ref. Interval"
            chunk = row[i:i + span]
            if len(chunk) < span:
                continue
            key = norm_key(" ".join(t.text for t in chunk))
            for role, labels in HEADER_WORDS.items():
                if role not in anchors and key in labels:
                    anchors[role] = chunk[0].bbox[0]
                    i += span
                    matched = True
                    break
            if matched:
                break
        if not matched:
            i += 1
    return anchors if len(anchors) >= 3 and "name" in anchors and "value" in anchors else None


def _column(tok: _Tok, anchors: dict[str, int], tol: int) -> str:
    best = None
    for role, x in sorted(anchors.items(), key=lambda kv: kv[1]):
        if tok.bbox[0] + tol >= x:
            best = role
    return best or "name"


def _join(toks: list[_Tok]) -> str:
    return " ".join(t.text for t in toks).strip()


def _region(role: str, page_index: int, toks: list[_Tok]) -> list[Region]:
    if not toks:
        return []
    by_line: dict[str, list[_Tok]] = {}
    for t in toks:
        by_line.setdefault(t.line_id, []).append(t)
    out = []
    for line_id, ts in by_line.items():  # one region per contributing line; never an invented union across lines
        gran = "word" if all(t.granularity == "word" for t in ts) else "line"
        box = bbox_union([t.bbox for t in ts])
        # When the field is the WHOLE OCR line (usual for a value cell), the line box is equally specific and
        # fully contains the glyphs; engine word boxes are estimated from character positions and can sit a
        # few pixels inside them (measured 2026-10-01).
        if gran == "word" and ts[0].line_bbox is not None and len(ts) == ts[0].line_words:
            box = ts[0].line_bbox
        out.append(Region(role, page_index, box, line_id, gran))
    return out


def _min_score(toks: list[_Tok]) -> float | None:
    scores = [t.score for t in toks if t.score is not None]
    return min(scores) if scores else None


def _split_value_flag(val_toks: list[_Tok], flag_toks: list[_Tok]) -> tuple[list[_Tok], list[_Tok]]:
    """A printed flag glued to the value column ("85,000 L") belongs to the flag field."""
    if len(val_toks) > 1 and parse_flag(val_toks[-1].text):
        return val_toks[:-1], flag_toks + [val_toks[-1]]
    return val_toks, flag_toks


def _candidate(page: PageOCR, row_index: int, cols: dict[str, list[_Tok]], flags: list[str]) -> LabCandidate | None:
    name_raw = _join(cols["name"]).rstrip(" :")  # "Haemoglobin:" → "Haemoglobin" (the key lookup ignores it anyway)
    val_toks, flag_toks = _split_value_flag(cols["value"], cols["flag"])
    value_raw = _join(val_toks)
    key = analyte_key(name_raw)
    if not value_raw and key is None:
        return None  # section title, footer, address line: not a result row
    if not name_raw:
        return None
    value = parse_value(value_raw)
    unit = parse_unit(_join(cols["unit"]))
    rng = parse_range(_join(cols["range"]))
    flag_raw = _join(flag_toks)
    regions: list[Region] = []
    for role, toks in (("name", cols["name"]), ("value", val_toks), ("unit", cols["unit"]), ("range", cols["range"]), ("flag", flag_toks)):
        regions += _region(role, page.page_index, toks)
    cand_flags = list(flags) + list(value.flags) + list(rng.flags)
    cand_flags += list(unit.flags)  # unit_missing when the unit column is empty, unit_unknown otherwise
    if key is None:
        cand_flags.append("unrecognized_analyte")
    if not value_raw:
        cand_flags.append("value_missing")
    if not cols["range"]:
        cand_flags.append("range_missing")
    if any(r.granularity == "line" for r in regions):
        cand_flags.append("region_line_level")
    if flag_raw and parse_flag(flag_raw) is None:
        cand_flags.append("flag_unparsed")
    return LabCandidate(
        page_index=page.page_index, row_index=row_index, name_raw=name_raw, analyte_key=key, value=value, unit=unit,
        range=rng, flag_raw=flag_raw, printed_flag=parse_flag(flag_raw), regions=regions, value_score=_min_score(val_toks),
        flags=sorted(set(cand_flags)),
    )


def _pattern_columns(row: list[_Tok]) -> dict[str, list[_Tok]]:
    """No header: name = leading non-numeric words; then value, unit, range, flag by pattern."""
    cols: dict[str, list[_Tok]] = {r: [] for r in ROLES}
    i = 0
    while i < len(row) and not any(ch.isdigit() for ch in row[i].text) and parse_value(row[i].text).kind != "qualitative":
        cols["name"].append(row[i])
        i += 1
    if i < len(row):
        cols["value"].append(row[i])
        i += 1
    if i < len(row) and unit_key(row[i].text):
        cols["unit"].append(row[i])
        i += 1
    rest = row[i:]
    if rest and parse_flag(rest[-1].text):
        cols["flag"].append(rest.pop())
    cols["range"] = rest
    return cols


def extract_lab(page: PageOCR) -> list[LabCandidate]:
    rows = _rows(_tokens(page))
    tol = max(4, page.width // 100)
    anchors: dict[str, int] | None = None
    out: list[LabCandidate] = []
    for ri, row in enumerate(rows):
        header = _header_anchors(row)
        if header:
            anchors = header  # repeated headers on later pages reset the columns
            continue
        if anchors is None:
            continue  # above the first header: patient/lab block, not results
        cols: dict[str, list[_Tok]] = {r: [] for r in ROLES}
        for t in row:
            cols[_column(t, anchors, tol)].append(t)
        cand = _candidate(page, ri, cols, [])
        if cand:
            out.append(cand)
    if anchors is None:  # no header anywhere on the page: pattern fallback, flagged
        for ri, row in enumerate(rows):
            if not any(any(ch.isdigit() for ch in t.text) for t in row):
                continue
            cand = _candidate(page, ri, _pattern_columns(row), ["no_column_header"])
            if cand and cand.analyte_key:
                out.append(cand)
    return out


# ── prose results (discharge summaries) ───────────────────────────────────────────────────────────
# Discharge summaries list investigations as running text: "Serum Sodium:132 mmol/L, Serum Potassium:3.6
# mmol/L, ...". A pair is taken only when its name resolves EXACTLY to a known analyte (lexicon); an unknown
# name ("C-Reactive Protein (CRP)", "Total W.B.C. Count") is skipped, never mapped to the closest test.
_PROSE_PAIR = re.compile(
    r"(?:^|[,;])\s*(?P<name>[^:,;=\n]{2,60}?)\s*[:=]\s*(?P<value>[<>≤≥]?\s*\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?P<unit>[A-Za-zµμ%/][^\s,;]*))?",
    re.MULTILINE,
)
_PROSE_PREFIX = re.compile(r"^(?:serum|s|blood|plasma)\.?\s+", re.IGNORECASE)


def _prose_analyte(name: str) -> tuple[str | None, str]:
    """(analyte_key, the printed words that matched). Tries the name as printed, without a parenthetical,
    the parenthetical itself ("Glycosylated Hb (HbA1c)"), and each without a "Serum"/"Blood" prefix."""
    forms = [name, re.sub(r"\(.*?\)", " ", name), *re.findall(r"\((.*?)\)", name)]
    forms += [_PROSE_PREFIX.sub("", f.strip()) for f in forms]
    for f in forms:
        f = f.strip(" .-")
        if f and (k := analyte_key(f)):
            return k, f
    return None, ""


def prose_pairs(text: str) -> list[tuple[str, str, str, str]]:
    """(analyte_key, printed name, value text, unit text) for each recognised "Name: value unit" pair."""
    out = []
    for m in _PROSE_PAIR.finditer(text):
        key, matched = _prose_analyte(m.group("name"))
        if key:
            out.append((key, matched, m.group("value").strip(), (m.group("unit") or "").rstrip(".")))
    return out


def extract_lab_prose(page_index: int, text: str, bbox: BBox, line_id: str, engine: str) -> list[LabCandidate]:
    """Candidates from one text block. The source region is the whole block (line-level), so every field is
    capped at Amber and the reviewer finds the value on the highlighted block."""
    out = []
    for i, (key, name, value_raw, unit_raw) in enumerate(prose_pairs(text)):
        value, unit = parse_value(value_raw), parse_unit(unit_raw)
        regions = [Region(role, page_index, bbox, line_id, "line") for role, present in (("name", True), ("value", True), ("unit", bool(unit_raw))) if present]
        flags = {"prose_text", "region_line_level", "range_missing", *value.flags, *unit.flags}
        out.append(LabCandidate(
            page_index=page_index, row_index=i, name_raw=name, analyte_key=key, value=value, unit=unit,
            range=parse_range(""), flag_raw="", printed_flag=None, regions=regions, value_score=None,
            flags=sorted(flags), source_engine=engine,
        ))
    return out


def extract_dates(pages: list[PageOCR]) -> DocDates:
    dates = DocDates()
    for page in pages:
        for row in _rows(_tokens(page)):
            text = norm_key(_join(row))
            for field_name, labels in DATE_LABELS.items():
                if getattr(dates, field_name) is not None:
                    continue
                if not any(lbl in text for lbl in labels):
                    continue
                # the date token nearest to the right of the label within this row
                label_toks = [t for t in row if any(norm_key(t.text).startswith(lbl.split()[0]) for lbl in labels)]
                if not label_toks:
                    continue
                lx = label_toks[0].bbox[2]
                for t in row:
                    m = _DATE.search(t.text)
                    if m and t.bbox[0] >= lx:
                        setattr(dates, field_name, m.group(1))
                        dates.regions.append(Region("date", page.page_index, t.bbox, t.line_id, t.granularity))
                        break
    return dates


def page_from_lines(page_index: int, width: int, height: int, lines: list[Line]) -> PageOCR:
    return PageOCR(page_index, width, height, lines)


__all__ = ["LabCandidate", "Region", "DocDates", "extract_lab", "extract_lab_prose", "prose_pairs", "extract_dates", "Word", "Line", "PageOCR", "page_from_lines"]
