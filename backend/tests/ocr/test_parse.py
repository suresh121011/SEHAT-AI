"""Grammar for printed values, units, ranges and flags (docs/14 §5). Pure functions, no OCR engine."""

import pytest

from app.ocr.lexicon import analyte_key, unit_key
from app.ocr.parse import compare_to_range, parse_flag, parse_range, parse_unit, parse_value


@pytest.mark.parametrize("raw,value", [
    ("13.8", "13.8"), ("12", "12"), ("2,45,000", "245000"), ("85,000", "85000"), ("1,50,000", "150000"),
    ("7,200", "7200"), ("150,000", "150000"), ("0.01", "0.01"), (" 4.10 ", "4.10"),
])
def test_numeric_values_keep_exact_decimal_text(raw, value):
    v = parse_value(raw)
    assert (v.kind, v.value, v.raw) == ("numeric", value, raw)


@pytest.mark.parametrize("raw,comp,value", [("<0.01", "<", "0.01"), ("< 5", "<", "5"), ("≤ 3.5", "<=", "3.5"), (">1000", ">", "1000"), ("≥ 2", ">=", "2")])
def test_comparators(raw, comp, value):
    v = parse_value(raw)
    assert (v.kind, v.comparator, v.value) == ("numeric", comp, value)


@pytest.mark.parametrize("raw,flag", [
    ("1O.5", "ocr_char_confusion"), ("8S", "ocr_char_confusion"), ("l2.4", "ocr_char_confusion"),
    ("1,5", "comma_decimal_ambiguous"), ("1.2.3", "multiple_decimal_points"), ("12,34,5", "value_unparsed"),
])
def test_ambiguous_values_are_never_repaired(raw, flag):
    v = parse_value(raw)
    assert v.kind == "unreadable" and v.value is None and flag in v.flags and v.raw == raw


def test_negative_only_when_allowed():
    assert parse_value("-2.5").kind == "unreadable"
    assert parse_value("-2.5", allow_negative=True).value == "-2.5"
    assert parse_value("−2.5", allow_negative=True).value == "-2.5"  # Unicode minus


@pytest.mark.parametrize("raw,q", [("Non-Reactive", "non_reactive"), ("NEGATIVE", "negative"), ("Not Detected", "not_detected"), ("Trace", "trace")])
def test_qualitative(raw, q):
    assert parse_value(raw).qualitative == q


@pytest.mark.parametrize("raw,key", [("g/dL", "g/dL"), ("gm/dl", "g/dL"), ("/cumm", "/cumm"), ("mill/cumm", "10^6/µL"), ("x10³/μL", "10^3/µL"), ("mIU/L", "mIU/L"), ("µIU/mL", "µIU/mL"), ("mIU/mL", "mIU/mL")])
def test_units_unicode_normalised(raw, key):
    assert parse_unit(raw).key == key


def test_unit_missing_and_unknown_flagged():
    assert parse_unit("").flags == ("unit_missing",)
    assert parse_unit("furlongs").key is None and "unit_unknown" in parse_unit("furlongs").flags


@pytest.mark.parametrize("raw,kind,low,high", [
    ("13.0 - 17.0", "between", "13.0", "17.0"), ("1,50,000 - 4,10,000", "between", "150000", "410000"),
    ("0.7 to 1.3", "between", "0.7", "1.3"), ("< 0.04", "upper", None, "0.04"), ("Up to 40", "upper", None, "40"),
    ("> 60", "lower", "60", None), ("Non-Reactive", "qualitative", None, None),
])
def test_ranges(raw, kind, low, high):
    r = parse_range(raw)
    assert (r.kind, r.low, r.high) == (kind, low, high)


def test_range_population_specific_and_malformed():
    assert parse_range("M: 13-17 F: 12-16").kind == "multi"
    assert parse_range("Male 13 - 17").kind == "multi"
    inv = parse_range("17 - 13")
    assert inv.kind == "malformed" and "range_inverted" in inv.flags
    assert parse_range("13 ~ 17").kind == "malformed"
    assert parse_range("").kind == "none"


def test_range_with_unit_inside_cell():
    r = parse_range("13 - 17 g/dL")
    assert (r.kind, r.unit_key) == ("between", "g/dL")


@pytest.mark.parametrize("val,unit,rng,expected", [
    ("11.2", "g/dL", "12.0 - 15.0", "below_range"),
    ("13.8", "g/dL", "13.0 - 17.0", "within_range"),
    ("1.42", "mg/dL", "0.7 - 1.3", "above_range"),
    ("13.0", "g/dL", "13.0 - 17.0", "within_range"),          # bounds inclusive
    ("<0.01", "ng/mL", "< 0.04", "within_range"),
    ("<5", "mg/dL", "3 - 10", "not_comparable"),               # could be 4 (in) or 2 (below)
    ("<2", "mg/dL", "3 - 10", "below_range"),
    ("<2", None, "3 - 10", "not_comparable"),                 # no unit: never placed against a range
    ("<=3", "mg/dL", "3 - 10", "not_comparable"),              # could equal 3
    (">12", "mg/dL", "3 - 10", "above_range"),
    ("2.1", None, "150000 - 410000", "not_comparable"),     # missing/unknown unit (council)
    ("13.8", "g/dL", "", "range_unavailable"),
    ("13.8", "g/dL", "M: 13-17 F: 12-16", "not_comparable"),
    ("13.8", "g/dL", "17 - 13", "not_comparable"),
    ("13.8", "mmol/L", "13 - 17 g/dL", "not_comparable"),      # unit mismatch
    ("Non-Reactive", None, "Non-Reactive", "within_range"),
    ("Reactive", None, "Non-Reactive", "not_comparable"),
])
def test_compare_to_range(val, unit, rng, expected):
    assert compare_to_range(parse_value(val), unit, parse_range(rng)) == expected


def test_flags_and_analytes():
    assert (parse_flag("H"), parse_flag("low"), parse_flag("↑"), parse_flag("")) == ("H", "L", "H", None)
    assert analyte_key("Haemoglobin") == "hemoglobin"
    assert analyte_key("Total Leucocyte Count") == "wbc"
    assert analyte_key("SGPT (ALT)") == "alt"
    assert analyte_key("Haemoglobn") is None  # misspelling is never mapped to the closest name
    assert unit_key("furlongs") is None
