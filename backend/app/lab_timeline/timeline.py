"""The lab timeline builder (docs/22). Pure: no I/O.

Input: per visit, its completed documents (report and collection dates as printed) and the values a person confirmed or
corrected on identity-checked documents (`app.ocr.service.reviewed_items`), plus what is still unresolved. Output: one
row per test with its results in date order, and the gaps.

What it says, and what it never says:
- A result is dated by the report date printed on the report (read day-first, as Indian reports print it; a two-digit
  year is 20xx), else the collection date, else the visit date; `date_source` says which. Printed dates are read by the
  machine and nobody checks them, so each result says so (`date_machine_read`) with the date's page regions, and the
  gaps count them. A date that cannot be read, or that falls after the visit, is a gap and the visit date is used.
- A result is compared with the previous result of the same test only when both are plain numbers in the same unit:
  "higher", "lower" or "same", and only when the date order agrees with the order the reports were added (a misread date
  must not reverse a direction). Never "better", "worse", "improving" or "worsening": that is a clinical judgement.
- Range flags repeat what the OCR checks recorded: below or above the range printed on that report, or the cited
  reference interval (`app.rules.reference_ranges`). They never change triage or urgency.
- Values not yet checked by a person are never shown; they are counted as a gap.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

NOTE = ("View only. Checked document values never change triage or urgency. Differences are stated as higher or lower "
        "than the previous result, never as better or worse.")

_LABELS = {
    "hemoglobin": "Haemoglobin", "platelets": "Platelet count", "wbc": "White blood cells (WBC)", "rbc": "Red blood cells (RBC)",
    "hematocrit": "Haematocrit (PCV)", "mcv": "MCV", "esr": "ESR", "creatinine": "Creatinine", "urea": "Urea",
    "glucose_fasting": "Glucose, fasting", "glucose_pp": "Glucose, after meals", "glucose_random": "Glucose, random",
    "hba1c": "HbA1c", "cholesterol_total": "Total cholesterol", "triglycerides": "Triglycerides", "hdl": "HDL cholesterol",
    "ldl": "LDL cholesterol", "bilirubin_total": "Total bilirubin", "bilirubin_direct": "Direct bilirubin", "alt": "ALT (SGPT)",
    "ast": "AST (SGOT)", "alp": "Alkaline phosphatase", "tsh": "TSH", "t3": "T3 (total)", "t4": "T4 (total)", "uric_acid": "Uric acid",
    "sodium": "Sodium", "potassium": "Potassium", "chloride": "Chloride", "calcium": "Calcium", "albumin": "Albumin",
    "total_protein": "Total protein", "troponin_i": "Troponin I", "hbsag": "HBsAg",
}
_DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\s*$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_RANGE_FLAGS = {
    ("printed", "below_range"): ("below_printed_range", "Below the range printed on the report"),
    ("printed", "above_range"): ("above_printed_range", "Above the range printed on the report"),
    ("reference", "below_range"): ("below_reference_interval", "Below the cited reference interval"),
    ("reference", "above_range"): ("above_reference_interval", "Above the cited reference interval"),
}
_NOT_COMPARED = {"unit_differs": "Not compared with the previous result: different units",
                 "not_a_plain_number": "Not compared with the previous result: not a plain number",
                 "order_uncertain": "Not compared with the previous result: the report dates disagree with the order the reports were added"}
_COMPARABLE = {"below_range", "within_range", "above_range"}
_GAP_TEXT = {
    "awaiting_check": "{n} document value(s) are waiting to be checked by a person and are not shown",
    "document_not_checked": "{n} document(s) have not been confirmed as this patient's, so their values are not shown",
    "date_from_visit": "{n} result(s) are placed by the visit date: no report or collection date could be read",
    "date_unreadable": "{n} document(s) have a printed date that could not be read",
    "date_after_visit": "{n} document(s) show a date after the visit, so the visit date is used instead",
    "dates_machine_read": "{n} report date(s) were read by the machine and not checked by a person; they set the order shown",
}


def label(analyte: str) -> str:
    return _LABELS.get(analyte, analyte.replace("_", " "))


def parse_date(text: str | None) -> date | None:
    """Day-first numeric dates only (`12/10/2026`, `5-1-26`): the shapes the OCR reads (app/ocr/extract.py)."""
    if not text:
        return None
    m = _DATE.match(text)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _show_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def _plain_number(v: dict) -> Decimal | None:
    raw = v.get("value")
    if v.get("comparator") or v.get("qualitative") or raw is None or not _NUMBER.match(str(raw).strip()):
        return None
    try:
        return Decimal(str(raw).strip())
    except InvalidOperation:
        return None


def _shown(v: dict) -> str:
    text = f"{v.get('comparator') or ''}{v.get('value') if v.get('value') is not None else (v.get('qualitative') or '')}"
    return f"{text} {v['unit']}" if v.get("unit") else text


def _change(prev: dict, curr: dict) -> dict:
    if prev["doc"]["created_at"] > curr["doc"]["created_at"]:
        return {"not_compared": "order_uncertain", "text": _NOT_COMPARED["order_uncertain"]}
    a, b = _plain_number(prev["raw"]), _plain_number(curr["raw"])
    if a is None or b is None:
        return {"not_compared": "not_a_plain_number", "text": _NOT_COMPARED["not_a_plain_number"]}
    if (prev["raw"].get("unit") or "") != (curr["raw"].get("unit") or ""):
        return {"not_compared": "unit_differs", "text": _NOT_COMPARED["unit_differs"]}
    direction = "higher" if b > a else "lower" if b < a else "same"
    words = {"higher": "Higher than", "lower": "Lower than", "same": "Same as"}[direction]
    return {"direction": direction, "text": f"{words} the previous result ({_shown(prev['raw'])})"}


def _range_flags(ranges: dict | None) -> list[dict]:
    if not ranges:
        return [{"code": "no_range_comparison", "text": "Not compared with a range"}]
    out = []
    statuses = (("printed", ranges.get("printed_range_status")), ("reference", (ranges.get("reference") or {}).get("status")))
    for kind, status in statuses:
        hit = _RANGE_FLAGS.get((kind, status))
        if hit:
            out.append({"code": hit[0], "text": hit[1]})
    if not any(status in _COMPARABLE for _, status in statuses):  # no flag must never read as "within range"
        out.append({"code": "no_range_comparison", "text": "Not compared with a range"})
    return out


def build(visits: list[dict[str, Any]]) -> dict[str, Any]:
    """`visits`: [{case_id, visit_date (YYYY-MM-DD), current, documents: [{document_id, created_at, dates}], values,
    unresolved}]. Values are `reviewed_items` rows; only lab values are used."""
    gaps = {"awaiting_check": 0, "document_not_checked": 0, "date_from_visit": 0, "date_unreadable": 0, "date_after_visit": 0,
            "dates_machine_read": 0}
    docs: dict[str, dict] = {}
    for v in visits:
        visit_day = date.fromisoformat(v["visit_date"][:10])
        for d in v["documents"]:
            dates = d.get("dates") or {}
            report, collected = parse_date(dates.get("report_date")), parse_date(dates.get("collected_date"))
            after = [x for x in (report, collected) if x is not None and x > visit_day]
            report = None if report in after else report
            collected = None if collected in after else collected
            when, source = (report, "report_date") if report else (collected, "collected_date") if collected else (visit_day, "visit_date")
            if source == "visit_date" and after:
                gaps["date_after_visit"] += 1
            elif source == "visit_date" and (dates.get("report_date") or dates.get("collected_date")):
                gaps["date_unreadable"] += 1
            docs[d["document_id"]] = {"date": when, "date_source": source, "created_at": d["created_at"], "case_id": v["case_id"],
                                      "visit_date": v["visit_date"][:10], "current": bool(v.get("current")),
                                      "date_machine_read": source != "visit_date", "date_regions": dates.get("regions") or []}
        for u in v["unresolved"]:
            gaps["awaiting_check" if u.get("field_id") else "document_not_checked"] += 1

    by_test: dict[str, list[dict]] = {}
    for v in visits:
        for val in v["values"]:
            doc = docs.get(val["source"]["document_id"])
            if val.get("kind") != "lab" or doc is None or not val.get("name"):
                continue
            if doc["date_source"] == "visit_date":
                gaps["date_from_visit"] += 1
            by_test.setdefault(val["name"], []).append({"doc": doc, "raw": val["value"], "val": val})

    used_docs = {r["val"]["source"]["document_id"] for rows in by_test.values() for r in rows}
    gaps["dates_machine_read"] = sum(1 for d in used_docs if docs[d]["date_machine_read"])
    latest_doc = max(used_docs, key=lambda d: (docs[d]["date"], docs[d]["created_at"])) if used_docs else None

    tests = []
    for analyte, rows in by_test.items():
        rows.sort(key=lambda r: (r["doc"]["date"], r["doc"]["created_at"]))
        results = []
        for i, r in enumerate(rows):
            val, doc = r["val"], r["doc"]
            results.append({
                "date": doc["date"].isoformat(), "date_source": doc["date_source"],
                "date_machine_read": doc["date_machine_read"], "date_regions": doc["date_regions"],
                "value": r["raw"].get("value"), "comparator": r["raw"].get("comparator"), "qualitative": r["raw"].get("qualitative"),
                "unit": r["raw"].get("unit"), "shown": _shown(r["raw"]), "outcome": val["outcome"],
                "flags": _range_flags(val.get("ranges")), "change": _change(rows[i - 1], r) if i > 0 else None,
                "case_id": doc["case_id"], "visit_date": doc["visit_date"], "current_visit": doc["current"], "source": val["source"],
            })
        flags = []
        if latest_doc is not None and rows[-1]["doc"]["date"] < docs[latest_doc]["date"]:  # an earlier report, not a same-day one
            flags.append({"code": "not_in_latest_report", "text": f"Not in the latest report ({_show_date(docs[latest_doc]['date'])})"})
        tests.append({"analyte": analyte, "label": label(analyte), "results": results, "flags": flags})
    tests.sort(key=lambda x: x["label"].lower())

    return {
        "visits": [{"case_id": v["case_id"], "visit_date": v["visit_date"][:10], "current": bool(v.get("current"))}
                   for v in sorted(visits, key=lambda v: v["visit_date"])],
        "tests": tests,
        "gaps": [{"code": c, "count": n, "text": _GAP_TEXT[c].format(n=n)} for c, n in gaps.items() if n],
        "note": NOTE,
    }
