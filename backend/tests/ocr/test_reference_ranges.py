"""Sourced 12-test reference table (app/rules/reference_ranges.py). Descriptive flags only; never urgency."""

import ast
from pathlib import Path

import pytest

from app.rules import reference_ranges as rr

APP = Path(__file__).parents[2] / "app"


@pytest.mark.parametrize("key,value,unit,status,band", [
    ("hemoglobin", "11.2", "g/dL", "below_range", None),            # below both sex cut-offs (12/13)
    ("hemoglobin", "12.5", "g/dL", "not_comparable", None),         # below for men, not for women: sex unknown
    ("hemoglobin", "14.0", "g/dL", "not_below_cutoff", None),       # WHO gives a lower cut-off only: never "within"
    ("hemoglobin", "22.0", "g/dL", "not_below_cutoff", None),
    ("hemoglobin", "8.5", "g/dL", "below_range", "WHO 2024 Hb cut-off band: moderate"),
    ("hemoglobin", "7.95", "g/dL", "below_range", "WHO 2024 Hb cut-off band: severe"),
    ("platelets", "85000", "/cumm", "below_range", None),            # 75k–150k: CTCAE grade 1 not banded
    ("platelets", "60000", "/cumm", "below_range", "CTCAE v5 platelet count decreased: grade 2"),
    ("platelets", "85000", "10^3/µL", "not_comparable", None),      # different scale: never converted
    ("wbc", "4200", "/cumm", "below_range", None),                   # 4,500 lower limit (source), not docs' 4,000
    ("creatinine", "1.1", "mg/dL", "not_comparable", None),          # within for men, above for women
    ("creatinine", "1.42", "mg/dL", "above_range", None),
    ("glucose_fasting", "100", "mg/dL", "above_range", "ADA 2026 fasting plasma glucose 100–125 mg/dL band"),
    ("hba1c", "5.65", "%", "below_upper_cutoff", None),
    ("hba1c", "5.7", "%", "above_range", "ADA 2026 / ICMR 2018 A1C 5.7–6.4% band"),
    ("cholesterol_total", "199", "mg/dL", "below_upper_cutoff", None),
    ("cholesterol_total", "240", "mg/dL", "above_range", "NCEP ATP III: high"),
    ("tsh", "4.5", "µIU/mL", "within_range", None),                 # µIU/mL = mIU/L (same quantity)
    ("uric_acid", "7.5", "mg/dL", "not_comparable", None),
])
def test_evaluate(key, value, unit, status, band):
    out = rr.evaluate(key, value, None, unit)
    assert out["status"] == status and (out["band"]["label"] if out["band"] else None) == band
    assert out["citation"] == rr.LAB_SOURCES[out["source_id"]]


def test_comparator_results_and_missing_unit_not_compared():
    assert rr.evaluate("platelets", "50000", "<", "/cumm")["status"] == "not_comparable"
    assert rr.evaluate("platelets", "85000", None, None)["status"] == "not_comparable"


def test_troponin_has_no_universal_limit():
    assert rr.evaluate("troponin_i", "0.01", None, "ng/mL") is None
    assert "troponin_i" in rr.DOCS_DIFFERENCE


def test_every_reference_and_band_cites_a_known_source():
    for ref in rr.REFERENCES.values():
        assert ref.source_id in rr.LAB_SOURCES
        assert all(b.source_id in rr.LAB_SOURCES for b in ref.bands)


def test_triage_engine_never_imports_reference_ranges():
    for path in (APP / "rules").rglob("*.py"):
        if path.name == "reference_ranges.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module or ""] + [a.name for a in node.names]
                assert not any("reference_ranges" in n for n in names), path
