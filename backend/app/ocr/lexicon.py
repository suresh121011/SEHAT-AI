"""Plain-data lexicon for printed lab reports and prescriptions (docs/14 §5). No clinical logic here.

Matching is exact on a normalized key (lower-case, punctuation removed, single spaces). A name that is
not listed is kept as printed with `analyte_key=None`; it is never mapped to the "closest" analyte.
"""

from __future__ import annotations

import re
import unicodedata

# analyte_key -> printed names seen on Indian lab reports (normalized form is computed below).
ANALYTE_ALIASES: dict[str, tuple[str, ...]] = {
    "hemoglobin": ("haemoglobin", "hemoglobin", "hb", "hgb", "haemoglobin hb", "hemoglobin hb"),
    "platelets": ("platelet count", "platelets", "plt", "platelet count plt", "total platelet count"),
    "wbc": ("total leucocyte count", "total leukocyte count", "tlc", "wbc", "wbc count", "white blood cell count", "total wbc count", "total leucocyte count tlc"),
    "rbc": ("rbc count", "red blood cell count", "rbc", "total rbc count"),
    "hematocrit": ("haematocrit", "hematocrit", "pcv", "haematocrit pcv", "hematocrit pcv", "packed cell volume"),
    "mcv": ("mcv", "mean corpuscular volume"),
    "esr": ("esr", "erythrocyte sedimentation rate"),
    "creatinine": ("serum creatinine", "creatinine", "s creatinine"),
    "urea": ("blood urea", "urea", "serum urea"),
    "glucose_fasting": ("fasting blood sugar", "blood sugar fasting", "fbs", "glucose fasting", "fasting blood glucose", "fasting plasma glucose", "fpg"),
    "glucose_pp": ("post prandial blood sugar", "ppbs", "blood sugar pp", "glucose pp", "postprandial blood glucose"),
    "glucose_random": ("random blood sugar", "rbs", "blood sugar random", "glucose random"),
    "hba1c": ("hba1c", "glycated haemoglobin", "glycated hemoglobin", "glycosylated haemoglobin", "glycosylated hemoglobin", "hba1c glycated haemoglobin"),
    "cholesterol_total": ("total cholesterol", "cholesterol total", "serum cholesterol", "cholesterol"),
    "triglycerides": ("triglycerides", "serum triglycerides"),
    "hdl": ("hdl cholesterol", "hdl", "hdl c"),
    "ldl": ("ldl cholesterol", "ldl", "ldl c"),
    "bilirubin_total": ("total bilirubin", "bilirubin total", "serum bilirubin total", "s bilirubin total"),
    "bilirubin_direct": ("direct bilirubin", "bilirubin direct", "conjugated bilirubin"),
    "alt": ("sgpt", "alt", "sgpt alt", "alt sgpt", "alanine aminotransferase"),
    "ast": ("sgot", "ast", "sgot ast", "ast sgot", "aspartate aminotransferase"),
    "alp": ("alkaline phosphatase", "alp"),
    "tsh": ("tsh", "thyroid stimulating hormone", "tsh ultrasensitive", "ultrasensitive tsh"),
    "t3": ("t3", "total t3", "triiodothyronine"),
    "t4": ("t4", "total t4", "thyroxine"),
    "uric_acid": ("uric acid", "serum uric acid"),
    "sodium": ("sodium", "serum sodium", "na", "sodium na"),
    "potassium": ("potassium", "serum potassium", "k", "potassium k"),
    "chloride": ("chloride", "serum chloride", "cl"),
    "calcium": ("calcium", "serum calcium", "total calcium"),
    "albumin": ("albumin", "serum albumin"),
    "total_protein": ("total protein", "serum total protein", "protein total"),
    "troponin_i": ("troponin i", "troponin-i", "trop i", "cardiac troponin i", "hs troponin i", "high sensitivity troponin i"),
    "hbsag": ("hbsag", "hepatitis b surface antigen"),
}

# canonical unit key -> printed forms (compared after normalize_unit()).
UNITS: dict[str, tuple[str, ...]] = {
    "g/dL": ("g/dl", "gm/dl", "gms/dl", "g%", "gm%"),
    "mg/dL": ("mg/dl", "mg%"),
    "mmol/L": ("mmol/l",),
    "µmol/L": ("µmol/l", "umol/l"),
    "%": ("%",),
    "/cumm": ("/cumm", "/cu mm", "cells/cumm", "/mm3", "cells/mm3", "/µl", "/ul", "cells/µl", "cells/ul"),
    "lakhs/cumm": ("lakhs/cumm", "lakh/cumm", "lacs/cumm", "lakhs/cu mm"),
    "10^3/µL": ("10^3/µl", "x10^3/µl", "10^3/ul", "x10^3/ul", "10³/µl", "x10³/µl", "thou/µl", "thou/ul", "k/µl", "k/ul"),
    "10^6/µL": ("10^6/µl", "x10^6/µl", "10^6/ul", "x10^6/ul", "10⁶/µl", "x10⁶/µl", "mill/cumm", "million/cumm", "mil/cumm"),
    "fL": ("fl",),
    "pg": ("pg",),
    "mm/hr": ("mm/hr", "mm/1st hr", "mm/h", "mm in 1st hr"),
    "U/L": ("u/l", "iu/l", "units/l"),
    "mIU/L": ("miu/l",),
    "µIU/mL": ("µiu/ml", "uiu/ml"),
    "mIU/mL": ("miu/ml",),  # 1000 × µIU/mL — kept distinct on purpose
    "ng/mL": ("ng/ml",),
    "ng/dL": ("ng/dl",),
    "µg/dL": ("µg/dl", "ug/dl", "mcg/dl"),
    "pg/mL": ("pg/ml",),
}

QUALITATIVE: dict[str, str] = {
    "positive": "positive", "negative": "negative", "reactive": "reactive", "non reactive": "non_reactive",
    "nonreactive": "non_reactive", "detected": "detected", "not detected": "not_detected", "nil": "nil",
    "absent": "absent", "present": "present", "trace": "trace",
}

HEADER_WORDS: dict[str, tuple[str, ...]] = {
    "name": ("test", "test name", "investigation", "parameter", "test description", "investigations"),
    "value": ("result", "results", "value", "observed value", "observed"),
    "unit": ("unit", "units"),
    "range": ("reference range", "reference", "ref range", "normal range", "biological reference interval", "biological ref interval", "reference interval", "normal value", "reference value", "range"),
    "flag": ("flag", "status"),
}

DATE_LABELS: dict[str, tuple[str, ...]] = {
    "collected_date": ("collected", "collection date", "sample collected", "collected on", "sample date", "date of collection"),
    "report_date": ("reported", "report date", "reported on", "date of report"),
}

FLAG_TOKENS: dict[str, str] = {"h": "H", "high": "H", "↑": "H", "l": "L", "low": "L", "↓": "L", "*": "abnormal"}

DOSAGE_FORMS = ("tab", "tab.", "tablet", "cap", "cap.", "capsule", "syp", "syp.", "syrup", "inj", "inj.", "injection", "drops", "oint", "cream", "susp")


def norm_key(text: str) -> str:
    """NFKC, lower-case, punctuation to spaces, collapsed whitespace."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"[()\[\]{},:;._\-–—/]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_unit(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).strip().lower()
    t = t.replace("μ", "µ").replace("³", "^3").replace("⁶", "^6").replace("×", "x").replace(" ", "")
    return t


_ALIAS_INDEX = {norm_key(a): k for k, aliases in ANALYTE_ALIASES.items() for a in aliases}
_UNIT_INDEX = {normalize_unit(u): k for k, forms in UNITS.items() for u in forms}
for _k in UNITS:
    _UNIT_INDEX.setdefault(normalize_unit(_k), _k)


def analyte_key(printed_name: str) -> str | None:
    return _ALIAS_INDEX.get(norm_key(printed_name))


def unit_key(printed_unit: str) -> str | None:
    return _UNIT_INDEX.get(normalize_unit(printed_unit))
