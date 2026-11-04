"""P0-1 regressions (docs/implementation/phase-0-baseline.md): a printed H/L flag that lands in the test-name column, and
explanation paragraphs read as result rows.

The layout mirrors a real synthetic kidney-function report read by PaddleOCR (sehat.db, 2026-11-04): header
TEST | VALUE | UNIT | REFERENCE with no FLAG column, every word its own OCR line, and the "L" printed just left of
the value column, so it falls inside the name column.
"""

from app.ocr.extract import extract_lab
from app.ocr.surya_parse import TableRow
from app.ocr.types import Line, PageOCR, Word

WIDTH, HEIGHT = 595, 842
X = {"name": 17, "flag": 200, "value": 220, "unit": 322, "range": 398}
_HEADER = (("TEST", X["name"]), ("VALUE", X["value"]), ("UNIT", X["unit"]), ("REFERENCE", X["range"]))


def _line(i: int, text: str, x: int, y: int) -> Line:
    box = (x, y, x + 8 * len(text), y + 13)
    return Line(f"L{i}", text, box, 0.99, (Word(text, box, 0.99),))


def page(rows: list[list[tuple[str, int]]], *, header=_HEADER) -> PageOCR:
    """Each row: (text, x) cells at one y; a name cell of several words becomes one OCR line per word, as observed."""
    lines: list[Line] = []
    all_rows = ([list(header)] if header else []) + rows
    for r, cells in enumerate(all_rows):
        y = 100 + 16 * r
        for text, x in cells:
            for k, w in enumerate(text.split() if x == X["name"] else [text]):
                lines.append(_line(len(lines), w, x + 50 * k if x == X["name"] else x, y))
    return PageOCR(0, WIDTH, HEIGHT, lines)


def result(name: str, value: str, unit: str = "", rng: str = "", flag: str = "") -> list[tuple[str, int]]:
    cells = [(name, X["name"])] + ([(flag, X["flag"])] if flag else []) + [(value, X["value"])]
    return cells + ([(unit, X["unit"])] if unit else []) + ([(rng, X["range"])] if rng else [])


def by_key(cands):
    return {c.analyte_key: c for c in cands}


# ── Test A: flag appended to the test name ──────────────────────────────────────────────────────────────────

def test_low_flag_in_name_column_is_split_and_row_is_recognised():
    ocr = page([result("SERUM UREA", "12", "mg/dl", "15-40", flag="L")])
    [c] = extract_lab(ocr)
    assert c.name_raw == "SERUM UREA"
    assert c.analyte_key == "urea"
    assert (c.value.value, c.unit.key, c.range.raw, c.range.low, c.range.high) == ("12", "mg/dL", "15-40", "15", "40")
    assert (c.flag_raw, c.printed_flag) == ("L", "L")
    assert "unrecognized_analyte" not in c.flags
    assert "flag_in_name_column" in c.flags  # provenance: the flag was taken from the name column


def test_split_flag_keeps_its_own_source_region():
    ocr = page([result("SERUM UREA", "12", "mg/dl", "15-40", flag="L")])
    [c] = extract_lab(ocr)
    flag_word = next(ln for ln in ocr.lines if ln.text == "L")
    assert [r.bbox for r in c.regions if r.role == "flag"] == [flag_word.bbox]
    assert all(r.bbox != flag_word.bbox for r in c.regions if r.role == "name")


# ── Test B: both flag values, no flag column, and no fabricated flag ────────────────────────────────────────

def test_high_and_low_flags_without_a_flag_column():
    ocr = page([result("SERUM CREATININE", "0.69", "mg/dl", "0.72-1.18", flag="L"),
                result("SERUM POTASSIUM", "5.8", "mmol/L", "3.5-5.1", flag="H"),
                result("SERUM SODIUM", "139", "mmol/L", "136-146")])
    c = by_key(extract_lab(ocr))
    assert (c["creatinine"].name_raw, c["creatinine"].printed_flag) == ("SERUM CREATININE", "L")
    assert (c["potassium"].name_raw, c["potassium"].printed_flag) == ("SERUM POTASSIUM", "H")
    assert (c["sodium"].flag_raw, c["sodium"].printed_flag) == ("", None)


def test_trailing_flag_like_token_is_not_stripped_when_the_name_is_still_unknown():
    # "EGFR" is not in the lexicon: removing "L" would not produce a recognised test, so nothing is split and no flag
    # is invented. The row stays unrecognised for human review.
    ocr = page([result("EGFR", "70", "", "> 90", flag="L")])
    [c] = extract_lab(ocr)
    assert c.name_raw == "EGFR L" and c.analyte_key is None
    assert (c.flag_raw, c.printed_flag) == ("", None)
    assert "unrecognized_analyte" in c.flags and "flag_in_name_column" not in c.flags


def test_explicit_flag_column_still_wins():
    header = _HEADER + (("FLAG", 500),)
    ocr = page([result("SERUM UREA", "12", "mg/dl", "15-40") + [("L", 500)]], header=header)
    [c] = extract_lab(ocr)
    assert (c.name_raw, c.analyte_key, c.printed_flag) == ("SERUM UREA", "urea", "L")
    assert "flag_in_name_column" not in c.flags


# ── Test C: explanation paragraphs are not result rows ──────────────────────────────────────────────────────

KFT_WITH_TEXT = [
    result("SERUM UREA", "17", "mg/dl", "19-45", flag="L"),
    [("Creatinine is a nitrogenous waste product formed", X["name"]), ("in muscle from creatine phosphate.", X["value"])],
    result("SERUM CREATININE", "0.69", "mg/dl", "0.72-1.18", flag="L"),
    [("Causes of Increased Serum Creatinine Level", X["name"]), ("Causes of Decreased", X["value"])],
    [("1. Pre-renal, renal, and post-renal azotemia", X["name"]), ("1. Pregnancy", X["value"])],
    [("GFR is measured to (i) detect suspected incipient", X["name"]), ("kidney disease (i.e. early detection),", X["value"])],
    result("SERUM CALCIUM", "8.9", "mg/dl", "8.8-10.6"),
    [("Mr. Sachin Sharma", X["name"]), ("Powered by Labsmart", X["value"])],
    [("Work timings", X["name"]), ("Monday to Sunday, 8 am", X["value"])],
]


def test_explanation_paragraphs_and_footer_are_not_lab_rows():
    cands = extract_lab(page(KFT_WITH_TEXT))
    assert [c.analyte_key for c in cands] == ["urea", "creatinine", "calcium"]
    assert not any(w in c.name_raw for c in cands for w in ("Creatinine is", "Causes", "Pre-renal", "GFR is", "Sachin", "Work"))


def test_results_after_a_paragraph_are_still_parsed_with_their_values():
    c = by_key(extract_lab(page(KFT_WITH_TEXT)))
    assert (c["creatinine"].value.value, c["creatinine"].printed_flag) == ("0.69", "L")
    assert (c["calcium"].value.value, c["calcium"].range.raw, c["calcium"].printed_flag) == ("8.9", "8.8-10.6", None)


# ── Test D/E: unrecognised but genuine rows are kept for review ─────────────────────────────────────────────

def test_unknown_test_with_a_number_or_a_short_code_is_kept_for_review():
    ocr = page([result("EGFR", "134.19", "ml/min/1.73m^2", "> 90"), result("EGFR CATEGORY", "G1"),
                result("BUN/ CREATININE RATIO", "11.51")])
    names = {c.name_raw: c for c in extract_lab(ocr)}
    assert set(names) == {"EGFR", "EGFR CATEGORY", "BUN/ CREATININE RATIO"}
    assert all(c.analyte_key is None and "unrecognized_analyte" in c.flags for c in names.values())
    assert names["EGFR CATEGORY"].value.raw == "G1"


def test_known_test_with_unreadable_value_is_never_dropped():
    ocr = page([result("HAEMOGLOBIN", "1O.8 g", "g/dl", "13-17")])
    [c] = extract_lab(ocr)
    assert c.analyte_key == "hemoglobin" and c.value.kind == "unreadable"


def test_second_engine_row_with_flag_in_name_cell_matches_the_same_analyte():
    # Surya may also return "SERUM UREA L" in the name cell; cross-engine matching is by analyte key.
    assert TableRow({"name": "SERUM UREA L", "value": "17"}, (0, 0, 1, 1), 0.9).analyte_key == "urea"
    assert TableRow({"name": "SERUM UREA", "value": "17"}, (0, 0, 1, 1), 0.9).analyte_key == "urea"
    assert TableRow({"name": "EGFR L", "value": "70"}, (0, 0, 1, 1), 0.9).analyte_key is None
