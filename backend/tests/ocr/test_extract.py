"""Layout extraction replayed from fixture ground truth (no OCR engine): every drawn text item becomes an
OCR line, so these tests prove the row/column logic and per-role provenance, not OCR accuracy."""

import json
from pathlib import Path

import pytest

from app.ocr.extract import extract_dates, extract_lab
from app.ocr.types import Line, PageOCR, Word

FIX = Path(__file__).parents[1] / "fixtures" / "ocr"


def replay(name: str, page_index: int = 0, *, drop: set[str] = frozenset(), edit: dict | None = None) -> tuple[PageOCR, dict]:
    truth = json.loads((FIX / f"{name}.truth.json").read_text())
    p = truth["pages"][page_index]
    lines = []
    for i, d in enumerate(p["drawn"]):
        text = (edit or {}).get(d["text"], d["text"])
        if d["text"] in drop:
            continue
        box = tuple(d["bbox"])
        lines.append(Line(f"L{i}", text, box, 0.99, (Word(text, box, 0.99),)))
    return PageOCR(page_index, p["width"], p["height"], lines), p


def rows_of(p: dict) -> dict[int, dict[str, dict]]:
    rows: dict[int, dict[str, dict]] = {}
    for f in p["fields"]:
        if f["row"] is not None:
            rows.setdefault(f["row"], {})[f["role"]] = f
    return rows


@pytest.mark.parametrize("fixture,page", [("cbc_normal", 0), ("cbc_low_platelet", 0), ("two_page_scan", 0), ("two_page_scan", 1)])
def test_every_printed_row_is_extracted_with_exact_text_and_true_regions(fixture, page):
    ocr, truth = replay(fixture, page)
    cands = extract_lab(ocr)
    expected = rows_of(truth)
    assert len(cands) == len(expected)
    for cand, (_, row) in zip(cands, sorted(expected.items())):
        assert cand.name_raw == row["name"]["text"]
        assert cand.value.raw == row["value"]["text"]
        if "unit" in row:
            assert cand.unit.raw == row["unit"]["text"]
        assert cand.range.raw == row["range"]["text"]
        assert cand.flag_raw == row.get("flag", {}).get("text", "")
        for role, f in row.items():
            regs = [r for r in cand.regions if r.role == role]
            assert len(regs) == 1 and list(regs[0].bbox) == f["bbox"], (role, f["text"])


def test_low_platelet_row_values_and_printed_flags():
    ocr, _ = replay("cbc_low_platelet")
    c = {x.analyte_key: x for x in extract_lab(ocr)}
    plt = c["platelets"]
    assert (plt.value.value, plt.unit.key, plt.range.low, plt.range.high, plt.printed_flag) == ("85000", "/cumm", "150000", "410000", "L")
    assert c["hemoglobin"].value.value == "11.2" and c["hemoglobin"].printed_flag == "L"
    assert c["rbc"].printed_flag is None


def test_comparator_and_qualitative_rows_and_missing_unit():
    ocr, _ = replay("two_page_scan", 1)
    c = {x.analyte_key: x for x in extract_lab(ocr)}
    trop = c["troponin_i"]
    assert (trop.value.comparator, trop.value.value, trop.range.kind, trop.range.high) == ("<", "0.01", "upper", "0.04")
    hbs = c["hbsag"]
    assert hbs.value.qualitative == "non_reactive" and "unit_missing" in hbs.flags


def test_ambiguous_value_is_kept_raw_and_unreadable():
    ocr, _ = replay("cbc_normal", edit={"13.8": "1O.8"})
    hb = next(x for x in extract_lab(ocr) if x.analyte_key == "hemoglobin")
    assert hb.value.kind == "unreadable" and hb.value.value is None and hb.value.raw == "1O.8"
    assert "ocr_char_confusion" in hb.flags


def test_missing_value_cell_is_reported_not_invented():
    ocr, _ = replay("cbc_normal", drop={"85.6"})
    mcv = next(x for x in extract_lab(ocr) if x.analyte_key == "mcv")
    assert mcv.value.kind == "empty" and "value_missing" in mcv.flags
    assert not [r for r in mcv.regions if r.role == "value"]


def test_header_and_patient_block_are_not_results():
    ocr, _ = replay("cbc_normal")
    names = [x.name_raw for x in extract_lab(ocr)]
    assert not any("Patient" in n or "Collected" in n or "Page" in n or "End of report" in n for n in names)


def test_misspelled_test_name_is_not_mapped():
    ocr, _ = replay("cbc_normal", edit={"Haemoglobin": "Haemoglobn"})
    row = next(x for x in extract_lab(ocr) if x.value.raw == "13.8")
    assert row.analyte_key is None and "unrecognized_analyte" in row.flags


def test_no_header_falls_back_to_pattern_and_is_flagged():
    ocr, _ = replay("cbc_normal", drop={"Test Name", "Result", "Unit", "Reference Range", "Flag"})
    cands = extract_lab(ocr)
    assert cands and all("no_column_header" in c.flags for c in cands)
    hb = next(x for x in cands if x.analyte_key == "hemoglobin")
    assert hb.value.value == "13.8" and hb.unit.key == "g/dL"


def test_value_and_flag_in_one_line_are_split():
    ocr, truth = replay("cbc_low_platelet")
    # Merge the platelet value and its flag into a single OCR line, as an engine might.
    lines = list(ocr.lines)
    v = next(i for i, l in enumerate(lines) if l.text == "85,000")
    vb = lines[v].bbox
    fl = next(i for i, l in enumerate(lines) if l.text == "L" and abs(l.bbox[1] - vb[1]) < 5)
    fb = lines[fl].bbox
    merged = Line("M", "85,000 L", (vb[0], vb[1], fb[2], fb[3]), 0.9, (Word("85,000", vb, 0.9), Word("L", fb, 0.9)))
    lines = [l for i, l in enumerate(lines) if i not in (v, fl)] + [merged]
    plt = next(x for x in extract_lab(PageOCR(0, ocr.width, ocr.height, lines)) if x.analyte_key == "platelets")
    assert plt.value.value == "85000" and plt.printed_flag == "L"
    assert next(r for r in plt.regions if r.role == "flag").bbox == fb  # flag region = the flag word only


def test_dates_extracted_with_regions():
    ocr, truth = replay("cbc_low_platelet")
    d = extract_dates([ocr])
    assert (d.collected_date, d.report_date) == ("15/09/2026", "15/09/2026")
    assert len(d.regions) == 2
