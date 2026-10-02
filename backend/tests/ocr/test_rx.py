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


def test_numbered_handwritten_items_with_form_after_name_and_continuation_lines():
    """Layout of a handwritten ENT prescription read by Chandra (2026-10-02): circled item numbers after
    "Plan →", frequency in words, form written after the name, duration on the next line."""
    meds = _rx("O/E - vesicles over left pinna & over the", "Plan → ① Acyclovir 800mg 5 Times a day", "× 7 DAYS",
               "② SOPRADERM OINTMENT BID ×", "7 DAYS.", "BP : 120/80 Temp.: 98.2° Pulse : 70 min SPO2 : 99%")
    assert [m.drug_raw for m in meds] == ["Acyclovir", "SOPRADERM"]
    a, o = meds
    assert (a.strength_raw, a.frequency_raw, a.duration_raw, a.form) == ("800mg", "5 Times a day", "7 DAYS", None)
    assert a.line_text == "Plan → ① Acyclovir 800mg 5 Times a day\n× 7 DAYS" and "dosage_pattern_missing" not in a.flags
    assert (o.form, o.frequency_raw, o.duration_raw) == ("ointment", "BID", "7 DAYS")


def test_numbered_lines_without_any_dosing_signal_are_not_medications():
    assert _rx("1. Pain in left ear 2-3 days", "(2) Review after a week", "Ointment applied by patient yesterday") == []
    # a continuation line never stands alone
    assert _rx("× 7 DAYS") == []


def _verify(chandra_line, paddle_lines):
    from app.ocr.service import _verify_meds
    from app.ocr.types import Line, PageOCR

    page = PageOCR(0, 1200, 400, [Line(f"l{i}", t, (20, 20 + 40 * i, 1100, 50 + 40 * i), 0.9, ()) for i, t in enumerate(paddle_lines)])
    meds = extract_rx([RxLine(chandra_line, (10, 10, 1150, 200), 0, "c0")])
    return _verify_meds(meds, {0: page}, None)[0]


def test_strength_disagreement_between_engines_is_a_dispute():
    v = _verify("① Acyclovir 500mg 5 Times a day", ["①ACyCLOVIR SoOma", "5 Times a day"])
    assert v["disputed"] and "dispute" in v["caps"] and v["band"] != "accept"
    assert any(c.reason == "strength_differs" and c.status == "fail" for c in v["checks"])


def test_strength_agreement_passes():
    v = _verify("Tab. Paracetamol 650 mg 1-0-1", ["Tab. Paracetamol 650mg 1-0-1"])
    assert not v["disputed"] and any(c.reason == "strength_agreement" and c.status == "pass" for c in v["checks"])
