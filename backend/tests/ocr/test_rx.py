"""Prescription line extraction (docs/09 5.3). Text lines only; no OCR engine, no medical advice."""

from app.ocr.rx import RxLine, extract_rx


def _rx(*texts):
    return extract_rx([RxLine(t, (10, 10 + 40 * i, 900, 40 + 40 * i), 0, f"L{i}") for i, t in enumerate(texts)])


def test_typical_lines():
    meds = _rx("Rx", "1. Tab. Paracetamol 650 mg 1-0-1 x 5 days", "2) Cap Amoxycillin 500mg 1-1-1 for 7 days", "Syp. Cough syrup 10 ml TDS", "Advice: plenty of fluids")
    assert [m.drug_raw for m in meds] == ["Paracetamol", "Amoxycillin", "Cough syrup"]
    p = meds[0]
    assert (p.form, p.strength_raw, p.dosage_pattern, p.duration_raw) == ("tab", "650 mg", "1-0-1", "5 days")
    assert meds[1].dosage_pattern == "1-1-1" and meds[1].duration_raw == "7 days"
    assert meds[2].frequency_raw == "TDS" and meds[2].dosage_pattern is None


def test_line_without_form_but_with_pattern_and_missing_parts_flagged():
    meds = _rx("Metformin 500 mg 1-0-1", "Tab. 1-0-0")
    assert meds[0].drug_raw == "Metformin" and meds[0].form is None
    assert "drug_name_missing" in meds[1].flags


def test_dosage_pattern_spacing_and_dashes_normalised_raw_kept():
    m = _rx("Tab. Amlodipine 5 mg 1 – 0 – 0")[0]
    assert m.dosage_pattern == "1-0-0" and "1 – 0 – 0" in m.line_text


def test_non_medication_lines_skipped():
    assert _rx("Name: Zzyzx Canary", "Date: 15/09/2026", "Diagnosis: (not extracted)") == []
