"""Gödel verification semantics (architecture §10; docs/14 §5C). Engine-free: fake re-read and cells."""

from app.ocr.extract import extract_lab
from app.ocr.types import Cell, Line, PageOCR, Word
from app.ocr.verify import band_for, overall_confidence, verify_lab
from tests.ocr.test_extract import replay


def _with_scores(ocr: PageOCR, scores: dict[str, float]) -> PageOCR:
    lines = [Line(l.line_id, l.text, l.bbox, scores.get(l.text, 0.99), (Word(l.text, l.bbox, scores.get(l.text, 0.99)),)) for l in ocr.lines]
    return PageOCR(ocr.page_index, ocr.width, ocr.height, lines)


def _cells_from_lines(ocr: PageOCR, override: dict[str, str] | None = None) -> list[Cell]:
    """A table-aware second engine reading every printed item (optionally misreading some)."""
    return [Cell((override or {}).get(l.text, l.text), (l.bbox[0] - 4, l.bbox[1] - 4, l.bbox[2] + 4, l.bbox[3] + 4), 0.97, "surya") for l in ocr.lines]


def _run(ocr, *, reread=None, cells=True, override=None, reference=None):
    ocr.cells = _cells_from_lines(ocr, override) if cells else []
    return {v.candidate.analyte_key or v.candidate.name_raw: v for v in verify_lab(extract_lab(ocr), {0: ocr}, reread=reread, second_engine="surya" if cells else None, reference=reference)}


def test_clean_agreeing_values_are_accept_but_still_unreviewed_suggestions():
    ocr, _ = replay("cbc_normal")
    out = _run(ocr)
    hb = out["hemoglobin"]
    assert hb.band == "accept" and not hb.disputed and hb.field_confidence == 0.97
    assert {r.engine for r in hb.readings} == {"paddleocr", "surya"}
    assert any(c.check == "maker_voting" and c.status == "not_run" for c in hb.checks)


def test_low_word_confidence_triggers_2x_reread_and_disagreement_is_a_dispute_capped_at_amber():
    ocr, _ = replay("cbc_low_platelet")
    ocr = _with_scores(ocr, {"85,000": 0.62})
    calls = []

    def reread(page, bbox, zoom):
        calls.append((page, zoom))
        return ("58,000", 0.8)

    plt = _run(ocr, reread=reread)["platelets"]
    assert calls == [(0, 2.0)]
    assert plt.disputed and plt.band == "amber"
    assert [r.text for r in plt.readings if r.engine.startswith("paddleocr")] == ["85,000", "58,000"]  # both readings kept


def test_reread_agreement_is_labelled_agreement_not_verified():
    ocr, _ = replay("cbc_low_platelet")
    ocr = _with_scores(ocr, {"85,000": 0.62})
    plt = _run(ocr, reread=lambda p, b, z: ("85,000", 0.9))["platelets"]
    rr = next(c for c in plt.checks if c.check == "reread_2x")
    assert (rr.status, rr.reason) == ("pass", "agreement")
    assert plt.band == "amber"  # 0.62 is below 0.85: agreement never promotes past the weakest signal


def test_two_engines_agreeing_on_a_wrong_value_still_only_suggest():
    ocr, _ = replay("cbc_normal", edit={"13.8": "18.3"})  # both engines "see" a wrong synthetic value
    hb = _run(ocr)["hemoglobin"]
    assert hb.band == "accept" and hb.candidate.value.value == "18.3"
    # nothing here marks it verified: the API keeps review_status pending until a named human confirms


def test_second_engine_disagreement_is_dispute_with_both_readings():
    ocr, _ = replay("cbc_normal")
    hb = _run(ocr, override={"13.8": "18.8"})["hemoglobin"]
    assert hb.disputed and hb.band == "amber"
    assert {r.text for r in hb.readings} == {"13.8", "18.8"}


def test_single_engine_is_capped_at_amber():
    ocr, _ = replay("cbc_normal")
    hb = _run(ocr, cells=False)["hemoglobin"]
    assert hb.band == "amber" and "single_engine" in hb.caps


def test_unreadable_value_goes_to_human_entry():
    ocr, _ = replay("cbc_normal", edit={"13.8": "1O.8"})
    assert _run(ocr)["hemoglobin"].band == "human_entry"


def test_printed_flag_inconsistent_with_range_is_a_dispute():
    # Hb 13.8 is within the printed 13.0-17.0; add a printed "L" in the flag column of that row.
    ocr2, _ = replay("cbc_normal")
    lines = list(ocr2.lines)
    hb_line = next(l for l in lines if l.text == "13.8")
    x = 1110
    lines.append(Line("F", "L", (x, hb_line.bbox[1], x + 12, hb_line.bbox[3]), 0.99, (Word("L", (x, hb_line.bbox[1], x + 12, hb_line.bbox[3]), 0.99),)))
    hb = _run(PageOCR(0, ocr2.width, ocr2.height, lines))["hemoglobin"]
    chk = next(c for c in hb.checks if c.check == "printed_flag_consistency")
    assert chk.status == "fail" and hb.disputed


def test_reference_range_disagreement_with_printed_range_caps_amber():
    ocr, _ = replay("cbc_normal")
    ref = lambda key, value, comp, unit: {"source_id": "TEST_SOURCE", "status": "below_range", "band": None} if key == "hemoglobin" else None  # noqa: E731
    hb = _run(ocr, reference=ref)["hemoglobin"]
    assert (hb.printed_range_status, hb.reference_range_status) == ("within_range", "below_range")
    assert "printed_vs_reference_range" in hb.caps and hb.band == "amber" and hb.reference_source == "TEST_SOURCE"


def test_duplicate_analyte_with_different_values_is_disputed_both_kept():
    ocr, _ = replay("cbc_normal")
    lines = list(ocr.lines)
    hb_name = next(l for l in lines if l.text == "Haemoglobin")
    y = 1300
    for text, x in (("Haemoglobin", hb_name.bbox[0]), ("12.1", 520), ("g/dL", 690), ("13.0 - 17.0", 880)):
        lines.append(Line(f"D{x}", text, (x, y, x + 120, y + 28), 0.99, (Word(text, (x, y, x + 120, y + 28), 0.99),)))
    vs = [v for v in verify_lab(extract_lab(PageOCR(0, ocr.width, ocr.height, lines)), {0: PageOCR(0, ocr.width, ocr.height, lines)}, reread=None, second_engine=None, reference=None) if v.candidate.analyte_key == "hemoglobin"]
    assert len(vs) == 2 and all(v.disputed for v in vs)


def test_bands_and_overall_is_min():
    assert band_for(0.9, [], False) == "accept"
    assert band_for(0.9, ["dispute"], False) == "amber"
    assert band_for(0.6, [], False) == "amber"
    assert band_for(0.4, [], False) == "human_entry"
    assert band_for(None, [], False) == "amber"
    assert band_for(0.99, [], True) == "human_entry"
    ocr, _ = replay("cbc_low_platelet")
    vs = list(_run(_with_scores(ocr, {"85,000": 0.62})).values())
    assert overall_confidence(vs) == 0.62


def test_real_surya_rows_agree_with_paddle_replay_and_a_differing_unit_is_a_dispute():
    import json
    from pathlib import Path

    from app.ocr.surya_parse import table_rows

    surya = json.loads((Path(__file__).parents[1] / "fixtures" / "ocr" / "engine_outputs" / "surya_cbc_low_platelet.json").read_text())
    rows = table_rows(next(iter(surya.values()))[0]["blocks"])
    ocr, _ = replay("cbc_low_platelet")
    ocr.table_rows = rows
    out = {v.candidate.analyte_key: v for v in verify_lab(extract_lab(ocr), {0: ocr}, reread=None, second_engine="surya-ocr-2", reference=None)}
    assert all(next(c for c in v.checks if c.check == "second_engine_agreement").status == "pass" for v in out.values())
    assert out["platelets"].band == "accept" and {r.engine for r in out["platelets"].readings} == {"paddleocr", "surya-ocr-2"}
    ocr2, _ = replay("cbc_low_platelet", edit={"mill/cumm": "/cumm"})
    ocr2.table_rows = rows
    rbc = next(v for v in verify_lab(extract_lab(ocr2), {0: ocr2}, reread=None, second_engine="surya-ocr-2", reference=None) if v.candidate.analyte_key == "rbc")
    assert rbc.disputed and next(c for c in rbc.checks if c.check == "second_engine_agreement").reason == "surya-ocr-2:differs:unit"
