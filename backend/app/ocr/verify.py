"""Gödel self-verification (Chain-of-Verification) for OCR output — architecture §10, made explicit.

Steps (architecture §10 `GodelOCRVerifier`):
  1. word-level confidence: a value whose recognizer score is < 0.7 is re-read at 2× zoom; a different
     reading is a Dispute (both readings kept).
  2. drug names → RxNorm fuzzy match (prescriptions; see app/ocr/rxnorm.py).
  3. lab values → reference-range check: the report's printed range and the sourced 12-test table
     (app/rules/reference_ranges.py). Disagreement between the two caps the field at Amber.
  4. MAKER voting on critical values — Phase 6 (docs/09 §6.3); reported as `not_run` here.
Plus a cross-engine check: the table-aware second engine (Surya) must read the same value in the same cell.

What the numbers mean (docs/14 §5C):
- `field_confidence` = min of the available signals (recognizer score, re-read agreement, second-engine
  agreement). They are engine scores, NOT calibrated probabilities that a clinical value is correct.
- `overall_confidence` = min over fields — never a mean, so one bad value cannot hide.
- Bands: > 0.85 `accept` (pre-filled suggestion, still needs a named reviewer), 0.5–0.85 `amber` (show
  both readings, no default), < 0.5 `human_entry` (blank; the reviewer types it from the paper).
- Caps at `amber`: any Dispute, a value read by one engine only, printed vs sourced range disagreement,
  unrecognized analyte, line-level geometry. Missing signals (`not_run`) never raise a band.
- Agreement between readers does not establish correctness: same-family re-reads can repeat an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Literal

from app.ocr.extract import LabCandidate
from app.ocr.lexicon import norm_key
from app.ocr.parse import compare_to_range, parse_value
from app.ocr.types import BBox, Cell, PageOCR, center, contains_point

CheckStatus = Literal["pass", "warn", "fail", "not_run"]
Band = Literal["accept", "amber", "human_entry"]

WORD_CONFIDENCE_THRESHOLD = 0.7  # architecture §10 step 1
ACCEPT_ABOVE = 0.85  # architecture §10 confidence bands
HUMAN_BELOW = 0.5
REREAD_ZOOM = 2.0

# reread(page_index, bbox, zoom) -> (text, score) or None if the engine could not re-read
Reread = Callable[[int, BBox, float], "tuple[str, float | None] | None"]
# reference(analyte_key, value, comparator, unit_key) -> app.rules.reference_ranges.evaluate() dict, or None
Reference = Callable[[str, "str | None", "str | None", "str | None"], "dict | None"]


@dataclass(frozen=True)
class Check:
    check: str
    status: CheckStatus
    reason: str = ""


@dataclass(frozen=True)
class Reading:
    engine: str
    text: str
    bbox: BBox | None
    score: float | None


@dataclass
class Verified:
    candidate: LabCandidate
    checks: list[Check] = field(default_factory=list)
    readings: list[Reading] = field(default_factory=list)
    field_confidence: float | None = None
    band: Band = "human_entry"
    caps: list[str] = field(default_factory=list)
    printed_range_status: str = "range_unavailable"
    reference_range_status: str = "range_unavailable"
    reference_source: str | None = None
    reference: dict | None = None  # source, citation, population, band — descriptive only
    disputed: bool = False


def _same_value(a: str, b: str) -> bool:
    pa, pb = parse_value(a), parse_value(b)
    if pa.kind == "numeric" and pb.kind == "numeric":
        return pa.value == pb.value and pa.comparator == pb.comparator
    if pa.kind == "qualitative" and pb.kind == "qualitative":
        return pa.qualitative == pb.qualitative
    return re.sub(r"\s+", "", a).lower() == re.sub(r"\s+", "", b).lower()


def _value_region(c: LabCandidate):
    regs = [r for r in c.regions if r.role == "value"]
    return regs[0] if len(regs) == 1 else None


def _cell_for(cells: list[Cell], bbox: BBox) -> Cell | None:
    hits = [cell for cell in cells if contains_point(cell.bbox, center(bbox))]
    return min(hits, key=lambda c: (c.bbox[2] - c.bbox[0]) * (c.bbox[3] - c.bbox[1])) if hits else None


def _cell_value_text(cell_text: str, candidate: LabCandidate) -> str:
    """A table cell may hold "85,000 L"; compare only the value part."""
    toks = cell_text.split()
    if len(toks) > 1 and candidate.flag_raw and toks[-1] == candidate.flag_raw:
        toks = toks[:-1]
    return " ".join(toks)


def band_for(conf: float | None, caps: list[str], unreadable: bool) -> Band:
    if unreadable:
        return "human_entry"
    if conf is None:
        return "amber"  # no engine score: never "accept"
    band: Band = "accept" if conf > ACCEPT_ABOVE else ("amber" if conf >= HUMAN_BELOW else "human_entry")
    if caps and band == "accept":
        band = "amber"
    return band


def verify_lab(
    candidates: list[LabCandidate],
    pages: dict[int, PageOCR],
    *,
    reread: Reread | None,
    second_engine: str | None,
    reference: Reference | None,
) -> list[Verified]:
    out: list[Verified] = []
    keys: dict[str, set[str]] = {}
    for c in candidates:
        if c.analyte_key and c.value.kind == "numeric":
            keys.setdefault(c.analyte_key, set()).add(f"{c.value.comparator or ''}{c.value.value}")
    for c in candidates:
        v = Verified(c)
        signals: list[float] = []
        region = _value_region(c)
        unreadable = c.value.kind in ("unreadable", "empty")
        v.readings.append(Reading("paddleocr", c.value.raw, region.bbox if region else None, c.value_score))

        # geometry / source support
        if region is None:
            v.checks.append(Check("source_support", "fail", "no_single_value_region"))
            v.caps.append("no_value_region")
        else:
            v.checks.append(Check("source_support", "pass"))
        if "region_line_level" in c.flags:
            v.caps.append("region_line_level")

        # value grammar
        if unreadable:
            v.checks.append(Check("value_syntax", "fail", (c.value.flags or ("value_missing",))[0]))
        else:
            v.checks.append(Check("value_syntax", "pass"))

        # Step 1: word-level confidence → 2× re-read of low-confidence values
        if c.value_score is not None:
            signals.append(c.value_score)
            if c.value_score < WORD_CONFIDENCE_THRESHOLD:
                rr = reread(c.page_index, region.bbox, REREAD_ZOOM) if (reread and region) else None
                if rr is None:
                    v.checks.append(Check("reread_2x", "not_run", "reread_unavailable"))
                else:
                    v.readings.append(Reading("paddleocr_2x", rr[0], region.bbox if region else None, rr[1]))
                    if _same_value(rr[0], c.value.raw):
                        v.checks.append(Check("reread_2x", "pass", "agreement"))
                    else:
                        v.checks.append(Check("reread_2x", "fail", "readings_differ"))
                        v.disputed = True
            else:
                v.checks.append(Check("word_confidence", "pass"))
        else:
            v.checks.append(Check("word_confidence", "not_run", "engine_gave_no_score"))

        # Cross-engine: the table-aware engine's independent reading of the same row (architecture: "Surya /
        # PaddleOCR (table-aware)"). Rows are matched by test name; value, unit and printed range are compared.
        page = pages.get(c.page_index)
        rows = page.table_rows if page else []
        cells = page.cells if page else []
        if second_engine is None or not (rows or cells):
            v.checks.append(Check("second_engine_agreement", "not_run", "second_engine_unavailable"))
            v.caps.append("single_engine")
        elif rows:
            # Each structure-aware engine (Surya; for discharge summaries also Chandra) is checked on its own.
            by_engine: dict[str, list] = {}
            for r in rows:
                if (c.analyte_key and r.analyte_key == c.analyte_key) or norm_key(r.cells.get("name", "")) == norm_key(c.name_raw):
                    by_engine.setdefault(r.engine, []).append(r)
            if not by_engine:
                v.checks.append(Check("second_engine_agreement", "warn", "no_matching_row"))
                v.caps.append("single_engine")
            for eng, same in sorted(by_engine.items()):
                if len(same) != 1:
                    v.checks.append(Check("second_engine_agreement", "warn", f"{eng}:ambiguous_row_match"))
                    v.caps.append("single_engine")
                    continue
                r = same[0]
                v.readings.append(Reading(r.engine, r.cells.get("value", ""), r.bbox, r.score))
                diffs = []
                if not _same_value(r.cells.get("value", ""), c.value.raw):
                    diffs.append("value")
                if norm_key(r.cells.get("unit", "")) != norm_key(c.unit.raw):
                    diffs.append("unit")
                if re.sub(r"\s+", "", r.cells.get("range", "")) != re.sub(r"\s+", "", c.range.raw):
                    diffs.append("range")
                if diffs:
                    v.checks.append(Check("second_engine_agreement", "fail", f"{eng}:differs:" + ",".join(diffs)))
                    v.disputed = True
                else:
                    v.checks.append(Check("second_engine_agreement", "pass", f"{eng}:agreement"))
                    if r.score is not None:
                        signals.append(float(r.score))
        elif region is None:
            v.checks.append(Check("second_engine_agreement", "not_run", "no_value_region"))
        else:
            cell = _cell_for(cells, region.bbox)
            if cell is None:
                v.checks.append(Check("second_engine_agreement", "warn", "no_matching_cell"))
                v.caps.append("single_engine")
            else:
                text = _cell_value_text(cell.text, c)
                v.readings.append(Reading(cell.engine, text, cell.bbox, cell.score))
                if _same_value(text, c.value.raw):
                    v.checks.append(Check("second_engine_agreement", "pass", "agreement"))
                    if cell.score is not None:
                        signals.append(cell.score)
                else:
                    v.checks.append(Check("second_engine_agreement", "fail", "readings_differ"))
                    v.disputed = True

        # Duplicate analyte with different values
        if c.analyte_key and len(keys.get(c.analyte_key, ())) > 1:
            v.checks.append(Check("duplicate_analyte", "fail", "different_values_for_same_test"))
            v.disputed = True

        # Step 3: reference ranges — printed range and sourced table
        unit = c.unit.key
        v.printed_range_status = compare_to_range(c.value, unit, c.range)  # unit None (missing/unknown) → not_comparable
        if c.range.kind in ("malformed", "multi"):
            v.checks.append(Check("range_syntax", "warn", (c.range.flags or ("range_unparsed",))[0]))
        ref = reference(c.analyte_key, c.value.value, c.value.comparator, unit) if (reference and c.analyte_key) else None
        if ref is None:
            v.checks.append(Check("reference_range", "not_run", "no_sourced_range_for_test"))
        else:
            v.reference = ref
            v.reference_source = ref["source_id"]
            v.reference_range_status = ref["status"]
            comparable = {"below_range", "within_range", "above_range"}
            if v.printed_range_status in comparable and v.reference_range_status in comparable and v.printed_range_status != v.reference_range_status:
                v.checks.append(Check("reference_range", "warn", "printed_and_reference_disagree"))
                v.caps.append("printed_vs_reference_range")
            else:
                v.checks.append(Check("reference_range", "pass" if v.reference_range_status in comparable else "not_run", v.reference_range_status))

        # Printed flag vs the printed range
        if c.printed_flag in ("H", "L") and v.printed_range_status in ("below_range", "within_range", "above_range"):
            expected = {"H": "above_range", "L": "below_range"}[c.printed_flag]
            ok = v.printed_range_status == expected
            v.checks.append(Check("printed_flag_consistency", "pass" if ok else "fail", "" if ok else "flag_disagrees_with_range"))
            if not ok:
                v.disputed = True

        # Step 4: MAKER voting is Phase 6
        v.checks.append(Check("maker_voting", "not_run", "phase_6"))

        if c.analyte_key is None:
            v.caps.append("unrecognized_analyte")
        if v.disputed:
            v.caps.append("dispute")
        # A disagreement caps the field at amber ("show both readings", architecture §10); the per-engine
        # scores stay as they are so the reviewer sees what each engine reported.
        v.field_confidence = min(signals) if signals else None
        v.caps = sorted(set(v.caps))
        v.band = band_for(v.field_confidence, v.caps, unreadable)
        out.append(v)
    return out


def overall_confidence(fields: list[Verified]) -> float | None:
    vals = [f.field_confidence for f in fields if f.field_confidence is not None]
    return min(vals) if vals else None
