"""Lab timeline (docs/22; brief item 2 "timeline with flagged gaps"): the pure builder. Checked values only, dated by
the report, compared as higher / lower / same only when comparable, gaps named. Synthetic data only."""

from datetime import date

import pytest

from app.ai import guard
from app.lab_timeline import timeline as t


@pytest.mark.parametrize("text,expected", [
    ("12/10/2026", date(2026, 10, 12)),  # day first, as Indian reports print it
    ("5-1-2026", date(2026, 1, 5)),
    ("05.01.26", date(2026, 1, 5)),
    ("31/02/2026", None),  # not a real date
    ("12/10/202", None),  # a three-digit year is not read
    ("2026/10/12", None),  # not the printed day-month-year shape
    ("", None),
    (None, None),
])
def test_report_dates_are_read_day_first_or_not_at_all(text, expected):
    assert t.parse_date(text) == expected


def _value(analyte, value, unit="g/dl", doc="d1", field=None, comparator=None, qualitative=None, printed="within_range", reference="within_range", outcome="confirmed"):
    return {"field_id": field or f"{doc}-{analyte}", "kind": "lab", "name": analyte, "outcome": outcome,
            "value": {"value": value, "comparator": comparator, "qualitative": qualitative, "unit": unit},
            "ranges": {"printed_range_status": printed, "reference": {"status": reference}},
            "source": {"type": "ocr", "document_id": doc, "field_id": field or f"{doc}-{analyte}", "page_index": 0, "regions": [{"x0": 1}]}}


def _visit(case_id, visit_date, docs, values, current=False, unresolved=()):
    return {"case_id": case_id, "visit_date": visit_date, "current": current, "documents": docs, "values": values, "unresolved": list(unresolved)}


def _doc(doc_id, report=None, collected=None, created="2026-11-01T09:00:00+00:00", regions=None):
    return {"document_id": doc_id, "created_at": created, "dates": {"report_date": report, "collected_date": collected, "regions": regions or []}}


def test_results_are_grouped_by_test_and_ordered_by_report_date_across_visits():
    v1 = _visit("c1", "2026-09-01", [_doc("d1", report="01/09/2026")], [_value("hemoglobin", "9.8", doc="d1"), _value("platelets", "150000", "/ul", doc="d1")])
    v2 = _visit("c2", "2026-11-10", [_doc("d2", report="10/11/2026")], [_value("hemoglobin", "11.2", doc="d2")], current=True)
    out = t.build([v2, v1])
    hb = next(x for x in out["tests"] if x["analyte"] == "hemoglobin")
    assert hb["label"] == "Haemoglobin"
    assert [(r["date"], r["value"], r["case_id"]) for r in hb["results"]] == [("2026-09-01", "9.8", "c1"), ("2026-11-10", "11.2", "c2")]
    assert hb["results"][0]["change"] is None
    assert hb["results"][1]["change"] == {"direction": "higher", "text": "Higher than the previous result (9.8 g/dl)"}
    assert hb["results"][1]["date_source"] == "report_date" and hb["results"][1]["source"]["document_id"] == "d2"
    plt = next(x for x in out["tests"] if x["analyte"] == "platelets")
    assert plt["flags"] == [{"code": "not_in_latest_report", "text": "Not in the latest report (10 Nov 2026)"}]
    assert "flags" in hb and hb["flags"] == []


@pytest.mark.parametrize("prev,curr,expected", [
    (dict(value="9.8"), dict(value="9.8"), {"direction": "same"}),
    (dict(value="9.8"), dict(value="8.1"), {"direction": "lower"}),
    (dict(value="9.8"), dict(value="98", unit="g/l"), {"not_compared": "unit_differs"}),
    (dict(value="9.8"), dict(value="12", comparator=">"), {"not_compared": "not_a_plain_number"}),
    (dict(value=None, qualitative="Negative"), dict(value=None, qualitative="Positive"), {"not_compared": "not_a_plain_number"}),
])
def test_changes_are_only_stated_between_comparable_results(prev, curr, expected):
    docs = [_doc("d1", report="01/09/2026"), _doc("d2", report="01/10/2026")]
    v = _visit("c1", "2026-10-01", docs, [_value("hemoglobin", doc="d1", **prev), _value("hemoglobin", doc="d2", **curr)], current=True)
    change = t.build([v])["tests"][0]["results"][1]["change"]
    for k, val in expected.items():
        assert change[k] == val
    assert guard.check(change["text"]) is None


def test_range_flags_report_what_was_recorded_without_judgement():
    v = _visit("c1", "2026-10-01", [_doc("d1", report="01/10/2026")],
               [_value("hemoglobin", "8.1", doc="d1", printed="below_range", reference="below_range")], current=True)
    r = t.build([v])["tests"][0]["results"][0]
    assert [f["code"] for f in r["flags"]] == ["below_printed_range", "below_reference_interval"]
    for f in r["flags"]:
        assert guard.check(f["text"]) is None


def test_dates_fall_back_and_the_gaps_say_so():
    docs = [_doc("d1", report="report: unclear", collected="03/09/2026"), _doc("d2"), _doc("d3", report="45/45/2026")]
    vals = [_value("urea", "20", "mg/dl", doc="d1"), _value("urea", "22", "mg/dl", doc="d2"), _value("urea", "24", "mg/dl", doc="d3")]
    unresolved = [{"document_id": "d2", "field_id": "x", "state": "machine_read"}, {"document_id": "d2", "field_id": "y", "state": "unsure"},
                  {"document_id": "d4", "state": "attestation_missing"}]
    out = t.build([_visit("c1", "2026-11-01", docs, vals, current=True, unresolved=unresolved)])
    sources = [r["date_source"] for r in out["tests"][0]["results"]]
    assert sorted(sources) == ["collected_date", "visit_date", "visit_date"]
    gaps = {g["code"]: g["count"] for g in out["gaps"]}
    assert gaps == {"awaiting_check": 2, "document_not_checked": 1, "date_from_visit": 2, "date_unreadable": 1, "dates_machine_read": 1}
    for g in out["gaps"]:
        assert g["text"] and guard.check(g["text"]) is None


def test_no_urgency_and_no_unchecked_value_ever_appears():
    v = _visit("c1", "2026-10-01", [_doc("d1", report="01/10/2026")], [_value("hemoglobin", "8.1", doc="d1")], current=True,
               unresolved=[{"document_id": "d1", "field_id": "f9", "state": "machine_read"}])
    out = t.build([v])
    keys = set(out) | {k for x in out["tests"] for k in x} | {k for x in out["tests"] for r in x["results"] for k in r}
    assert not any("urgency" in k or "triage" in k for k in keys)  # the timeline has no urgency of any kind
    assert all(r["outcome"] in ("confirmed", "corrected") for x in out["tests"] for r in x["results"])
    assert "never change triage or urgency" in out["note"]


def test_every_known_analyte_has_a_plain_label():
    from app.ocr.lexicon import ANALYTE_ALIASES

    for analyte in ANALYTE_ALIASES:
        label = t.label(analyte)
        assert label and label != analyte.replace("_", " ") or analyte in ("mcv", "esr", "tsh"), analyte
        assert guard.check(label) is None
    assert t.label("some_unknown_test") == "some unknown test"


def test_same_day_reports_are_not_flagged_as_missing_from_the_latest():
    docs = [_doc("d1", report="10/11/2026", created="2026-11-10T09:00:00+00:00"), _doc("d2", report="10/11/2026", created="2026-11-10T10:00:00+00:00")]
    out = t.build([_visit("c1", "2026-11-10", docs, [_value("hemoglobin", "8", doc="d1"), _value("urea", "20", "mg/dl", doc="d2")], current=True)])
    assert all(x["flags"] == [] for x in out["tests"])


def test_a_misread_date_never_reverses_a_direction_and_a_date_after_the_visit_is_not_used():
    # "10/11/2026" misread as "10/1/2026": by date the second report would come first and 8 → 11 would read as "lower"
    docs = [_doc("d1", report="10/11/2026", created="2026-11-10T09:00:00+00:00"), _doc("d2", report="10/1/2026", created="2026-11-10T10:00:00+00:00"),
            _doc("d3", report="12/12/2026", created="2026-11-10T11:00:00+00:00")]
    vals = [_value("hemoglobin", "8", doc="d1"), _value("hemoglobin", "11", doc="d2"), _value("urea", "20", "mg/dl", doc="d3")]
    out = t.build([_visit("c1", "2026-11-10", docs, vals, current=True)])
    hb = next(x for x in out["tests"] if x["analyte"] == "hemoglobin")
    assert hb["results"][1]["change"]["not_compared"] == "order_uncertain" and "direction" not in hb["results"][1]["change"]
    urea = next(x for x in out["tests"] if x["analyte"] == "urea")
    assert urea["results"][0]["date_source"] == "visit_date" and urea["results"][0]["date"] == "2026-11-10"
    assert {g["code"]: g["count"] for g in out["gaps"]}["date_after_visit"] == 1


def test_machine_read_dates_are_marked_with_their_page_regions_and_counted():
    region = [{"page_index": 0, "x0": 10, "y0": 20, "x1": 90, "y1": 30}]
    out = t.build([_visit("c1", "2026-11-10", [_doc("d1", report="09/11/2026", regions=region)], [_value("hemoglobin", "9", doc="d1")], current=True)])
    r = out["tests"][0]["results"][0]
    assert r["date_machine_read"] is True and r["date_regions"] == region
    assert {g["code"]: g["count"] for g in out["gaps"]}["dates_machine_read"] == 1
    visit_dated = t.build([_visit("c1", "2026-11-10", [_doc("d1")], [_value("hemoglobin", "9", doc="d1")], current=True)])
    assert visit_dated["tests"][0]["results"][0]["date_machine_read"] is False


def test_a_value_with_no_comparable_range_says_so_instead_of_staying_silent():
    v = _visit("c1", "2026-10-01", [_doc("d1", report="01/10/2026")], [_value("hemoglobin", "9", doc="d1", printed="not_comparable", reference=None)], current=True)
    assert t.build([v])["tests"][0]["results"][0]["flags"] == [{"code": "no_range_comparison", "text": "Not compared with a range"}]
