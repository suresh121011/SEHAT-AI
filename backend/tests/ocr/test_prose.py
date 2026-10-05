"""Lab values written as running text in discharge summaries (docs/14). Synthetic text only."""

from pathlib import Path

from app.ocr.extract import extract_lab_prose, prose_pairs
from app.ocr.types import Line, PageOCR, Word

PROSE = ("Serum Sodium:132 mmol/L,Serum Potassium:3.6 mmol/L.,Serum Chlorides:106 mmol/L,Serum Total Protein:6.3 gm/dl,"
         "Serum Albumin:3.2 g/dl,Serum Bilirubin Total:0.37 mg/dl,SGOT:34 U/L,SGPT:20 U/L,Serum TSH (Ultra):0.8 µIU/mL,"
         "Glycosylated Hb (HbA1c):9.4 %,Haemoglobin:13.4 g/dl,Total W.B.C. Count:19000 /µL,Platelet Count:1,55,000 /µL,"
         "Serum Creatinine:1 mg/dl,C- Reactive Protein (CRP):170.09 mg/L")


def test_known_analytes_extracted_with_printed_value_and_unit():
    got = {k: (v, u) for k, _n, v, u in prose_pairs(PROSE)}
    assert got["sodium"] == ("132", "mmol/L") and got["potassium"] == ("3.6", "mmol/L")
    assert got["total_protein"] == ("6.3", "gm/dl") and got["bilirubin_total"] == ("0.37", "mg/dl")
    assert got["ast"] == ("34", "U/L") and got["alt"] == ("20", "U/L")
    assert got["tsh"] == ("0.8", "µIU/mL") and got["hba1c"] == ("9.4", "%")
    assert got["hemoglobin"] == ("13.4", "g/dl") and got["platelets"] == ("1,55,000", "/µL") and got["creatinine"] == ("1", "mg/dl")


def test_unknown_names_are_skipped_never_mapped_to_the_closest_test():
    keys = [k for k, *_ in prose_pairs(PROSE)]
    # "Serum Chlorides" (plural), "Total W.B.C. Count" and "C- Reactive Protein" are not lexicon names
    assert "chloride" not in keys and "wbc" not in keys and len(keys) == len(set(keys))
    assert prose_pairs("Page 1 of 3, Printed On: 04/05/2021, Age/Sex: 49 Yrs/Male, BP: 120/80 mmHg") == []


def test_candidates_are_line_level_chandra_readings_without_scores():
    cands = extract_lab_prose(0, "Haemoglobin:13.4 g/dl, Platelet Count:1,55,000 /µL", (10, 20, 900, 60), "p0c3", "chandra-ocr-2")
    hb, plt = cands
    assert hb.source_engine == "chandra-ocr-2" and hb.value_score is None and hb.value.value == "13.4" and hb.unit.key == "g/dL"
    assert plt.value.value == "155000"
    assert {"prose_text", "region_line_level", "range_missing"} <= set(hb.flags)
    assert {r.role for r in hb.regions} == {"name", "value", "unit"} and all(r.bbox == (10, 20, 900, 60) and r.granularity == "line" for r in hb.regions)


class _Engines:
    """Chandra reads the prose block; PaddleOCR's line inside it reads sodium differently (152 vs 132)."""

    def __init__(self, paddle_text):
        self.paddle_text = paddle_text

    def paddle_page(self, png, i):
        box = (40, 600, 1100, 640)
        return PageOCR(i, 1240, 1754, [Line("p0l0", self.paddle_text, box, 0.66, (Word(self.paddle_text, box, 0.66),))])

    def paddle_reread(self, png, bbox, zoom):
        return None

    def surya_page(self, png):
        return {"blocks": []}

    def chandra_page(self, png):
        return {"blocks": [{"label": "Text", "bbox": [30, 590, 1120, 660],
                            "html": "<p>Serum Sodium:132 mmol/L, Serum Potassium:3.6 mmol/L, Haemoglobin:13.4 g/dl</p>"}]}


def _run(paddle_text):
    from app.config import get_settings
    from app.ocr.service import run_pipeline

    png = (Path(__file__).parents[1] / "fixtures" / "ocr" / "cbc_low_platelet.png").read_bytes()
    out = run_pipeline(png, "discharge_summary", get_settings(), _Engines(paddle_text))
    return {f.candidate.analyte_key: f for f in out["lab"] if "prose_text" in f.candidate.flags}


def test_discharge_summary_prose_values_cross_checked_and_never_accepted(monkeypatch):
    monkeypatch.setenv("OCR_RETENTION_DAYS", "none")
    monkeypatch.setenv("OCR_RXNORM_DB", "/nonexistent")
    from app.config import get_settings

    get_settings.cache_clear()
    f = _run("Serum Sodium:152 mmol/L,Serum Potassium:3.6 mmol/L")
    assert set(f) == {"sodium", "potassium", "hemoglobin"}
    na, k, hb = f["sodium"], f["potassium"], f["hemoglobin"]
    assert na.disputed and "dispute" in na.caps and {r.engine for r in na.readings} == {"chandra-ocr-2", "paddleocr"}
    assert not k.disputed and any(c.check == "second_engine_agreement" and c.status == "pass" for c in k.checks)
    assert "single_engine" in hb.caps  # PaddleOCR did not read haemoglobin in this block
    assert all(v.band != "accept" for v in f.values())  # line-level region, no engine score: a human checks every value
    get_settings.cache_clear()


def _cand(page_index, key, flags, engine="paddleocr", name="Haemoglobin"):
    from app.ocr.extract import LabCandidate
    from app.ocr.parse import parse_range, parse_unit, parse_value

    return LabCandidate(page_index=page_index, row_index=0, name_raw=name, analyte_key=key, value=parse_value("11.2"), unit=parse_unit("g/dL"),
                        range=parse_range(""), flag_raw="", printed_flag=None, regions=[], value_score=None, flags=flags, source_engine=engine)


def test_prose_reading_replaces_the_header_less_duplicate_of_the_same_line():
    """Pre-Phase 9 walkthrough: a discharge summary showed "Haemoglobin:" (pattern fallback) AND "Haemoglobin" (prose)
    for one printed value, so a reviewer could confirm or contradict it twice."""
    from app.ocr.service import drop_prose_duplicates

    prose = [_cand(0, "hemoglobin", ["prose_text"], "chandra-ocr-2"), _cand(0, "platelets", ["prose_text"], "chandra-ocr-2")]
    table = [
        _cand(0, "hemoglobin", ["no_column_header"]),           # same page + analyte as a prose reading → dropped
        _cand(0, "creatinine", ["no_column_header"]),           # no prose reading of it → kept
        _cand(1, "hemoglobin", ["no_column_header"]),           # other page → kept
        _cand(0, "platelets", []),                              # header-anchored table row → always kept
    ]
    kept = drop_prose_duplicates(table, prose)
    assert [(c.page_index, c.analyte_key, c.flags) for c in kept] == [(0, "creatinine", ["no_column_header"]), (1, "hemoglobin", ["no_column_header"]), (0, "platelets", [])]
    assert drop_prose_duplicates(table, []) == table  # lab reports (no prose path) are untouched


def test_printed_name_trailing_colon_is_not_kept():
    from app.ocr.extract import _candidate, _Tok
    from app.ocr.types import PageOCR

    page = PageOCR(page_index=0, width=1000, height=1000, lines=[])
    tok = lambda t: _Tok(t, (0, 0, 10, 10), 0.99, "l0", "word")  # noqa: E731
    c = _candidate(page, 0, {"name": [tok("Haemoglobin:")], "value": [tok("11.2")], "unit": [tok("g/dL")], "range": [], "flag": []}, [])
    assert c is not None and c.name_raw == "Haemoglobin" and c.analyte_key == "hemoglobin"
