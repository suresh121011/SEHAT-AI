"""Sourced reference intervals for the architecture's "12 common Indian tests" (docs/09 §5.6; docs/14 §5).

NOT a triage rule. The triage engine (`evaluate_triage`) never imports this module (tests/rules/test_isolation),
and nothing here sets or changes urgency. It is used only to flag an OCR-read lab value against a cited
interval, next to the interval printed on the report, for a clinician to read.

Every number cites a primary or authoritative source fetched and quoted on 2026-10-01 (docs/14 §5.4 has
the verbatim quotes). Where docs/09's numbers differed from the source, the SOURCE value is used and the
difference is recorded in `DOCS_DIFFERENCE`. Sex-specific intervals are evaluated for both sexes (the case
record has no sex field): a status is reported only when both agree, otherwise `not_comparable`.

Troponin I is deliberately absent: the Fifth Universal Definition of MI (2026) requires assay- and
sex-specific 99th-percentile limits and gives no universal number — only the report's printed range applies.

Severity bands are given only where a published grading exists, are labelled with their source, and are
descriptive of the number only (never a diagnosis).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


LAB_SOURCES: dict[str, str] = {
    "WHO_HB_2024": "WHO. Guideline on haemoglobin cutoffs to define anaemia in individuals and populations. Geneva: WHO; 2024. Table 3.",
    "MEDLINEPLUS_CBC": "MedlinePlus. CBC blood test (reviewed 14 Oct 2024). https://medlineplus.gov/ency/article/003642.htm",
    "MEDLINEPLUS_PLT": "MedlinePlus. Platelet count (reviewed 3 Feb 2025). https://medlineplus.gov/ency/article/003647.htm",
    "MEDLINEPLUS_WBC": "MedlinePlus. WBC count (reviewed 3 Feb 2025). https://medlineplus.gov/ency/article/003643.htm",
    "MEDLINEPLUS_CREAT": "MedlinePlus. Creatinine blood test (reviewed 13 Jul 2025). https://medlineplus.gov/ency/article/003475.htm",
    "MEDLINEPLUS_GLU": "MedlinePlus. Blood sugar test (reviewed 10 Jan 2025). https://medlineplus.gov/ency/article/003482.htm",
    "ADA_SOC_2026": "American Diabetes Association. 2. Diagnosis and Classification of Diabetes: Standards of Care in Diabetes—2026. Diabetes Care 2026;49(Suppl 1). PMC12690183.",
    "ICMR_T2DM_2018": "ICMR Guidelines for Management of Type 2 Diabetes 2018, Table 3.1.",
    "NCEP_ATP3": "NHLBI/NCEP. ATP III Guidelines At-A-Glance Quick Desk Reference.",
    "MEDLINEPLUS_BILI": "MedlinePlus. Bilirubin blood test (reviewed 13 Feb 2025). https://medlineplus.gov/ency/article/003479.htm",
    "MEDLINEPLUS_ALT": "MedlinePlus. Alanine transaminase (ALT) blood test (reviewed 1 Jan 2025). https://medlineplus.gov/ency/article/003473.htm",
    "MEDLINEPLUS_TSH": "MedlinePlus. TSH test (reviewed 25 Jan 2026). https://medlineplus.gov/ency/article/003684.htm",
    "MEDLINEPLUS_URIC": "MedlinePlus. Uric acid - blood (reviewed 1 Apr 2025). https://medlineplus.gov/ency/article/003476.htm",
    "CTCAE_V5": "NCI. Common Terminology Criteria for Adverse Events (CTCAE) v5.0, 27 Nov 2017 (oncology adverse-event grading).",
}


@dataclass(frozen=True)
class Interval:
    low: str | None  # inclusive, exact decimal text
    high: str | None
    unit: str  # canonical unit key (app/ocr/lexicon.UNITS)
    high_exclusive: bool = False  # True where the source writes "< x"


@dataclass(frozen=True)
class Band:
    label: str  # descriptive, source-worded
    low: str | None  # inclusive
    high: str | None  # exclusive (sources write grades as "< x - y" / "<x")
    source_id: str


@dataclass(frozen=True)
class Reference:
    analyte_key: str
    source_id: str
    population: str  # who the interval applies to, as the source states it
    male: Interval | None = None
    female: Interval | None = None
    any_sex: Interval | None = None
    bands: tuple[Band, ...] = field(default=())
    note: str = ""


REFERENCES: dict[str, Reference] = {
    "hemoglobin": Reference(
        "hemoglobin", "WHO_HB_2024", "adults 15–65 y; men and non-pregnant women (not valid in pregnancy, <15 y or >65 y)",
        male=Interval("13.0", None, "g/dL"), female=Interval("12.0", None, "g/dL"),
        bands=(Band("WHO 2024 Hb cut-off band: moderate", "8.0", "11.0", "WHO_HB_2024"), Band("WHO 2024 Hb cut-off band: severe", None, "8.0", "WHO_HB_2024")),
        note="WHO gives lower cut-offs only (no upper limit). Mild band differs by sex and is not shown without sex.",
    ),
    "platelets": Reference("platelets", "MEDLINEPLUS_PLT", "general", any_sex=Interval("150000", "400000", "/cumm"),
                           bands=(Band("CTCAE v5 platelet count decreased: grade 2", "50000", "75000", "CTCAE_V5"), Band("CTCAE v5: grade 3", "25000", "50000", "CTCAE_V5"), Band("CTCAE v5: grade 4", None, "25000", "CTCAE_V5"))),
    "wbc": Reference("wbc", "MEDLINEPLUS_WBC", "general", any_sex=Interval("4500", "11000", "/cumm"),
                     bands=(Band("CTCAE v5 WBC decreased: grade 2", "2000", "3000", "CTCAE_V5"), Band("CTCAE v5: grade 3", "1000", "2000", "CTCAE_V5"), Band("CTCAE v5: grade 4", None, "1000", "CTCAE_V5"))),
    "creatinine": Reference("creatinine", "MEDLINEPLUS_CREAT", "adults", male=Interval("0.7", "1.3", "mg/dL"), female=Interval("0.5", "0.95", "mg/dL")),
    "glucose_fasting": Reference("glucose_fasting", "MEDLINEPLUS_GLU", "fasting, non-pregnant", any_sex=Interval("70", "99", "mg/dL"),
                                 bands=(Band("ADA 2026 fasting plasma glucose 100–125 mg/dL band", "100", "126", "ADA_SOC_2026"), Band("ADA 2026 fasting plasma glucose ≥126 mg/dL band", "126", None, "ADA_SOC_2026")),
                                 note="ICMR 2018 also lists the WHO cut-off (<110 mg/dL). A1C/FPG results need confirmation by a clinician."),
    "hba1c": Reference("hba1c", "ADA_SOC_2026", "non-pregnant adults", any_sex=Interval(None, "5.7", "%", high_exclusive=True),
                       bands=(Band("ADA 2026 / ICMR 2018 A1C 5.7–6.4% band", "5.7", "6.5", "ADA_SOC_2026"), Band("ADA 2026 / ICMR 2018 A1C ≥6.5% band", "6.5", None, "ADA_SOC_2026")),
                       note="ADA: A1C is less reliable with iron-deficiency anaemia and haemoglobinopathies."),
    "cholesterol_total": Reference("cholesterol_total", "NCEP_ATP3", "adults", any_sex=Interval(None, "200", "mg/dL", high_exclusive=True),
                                   bands=(Band("NCEP ATP III: borderline high", "200", "240", "NCEP_ATP3"), Band("NCEP ATP III: high", "240", None, "NCEP_ATP3"))),
    "bilirubin_total": Reference("bilirubin_total", "MEDLINEPLUS_BILI", "adults", any_sex=Interval("0.1", "1.2", "mg/dL")),
    "alt": Reference("alt", "MEDLINEPLUS_ALT", "adults", any_sex=Interval("4", "36", "U/L"), note="Lab-specific; many Indian labs print higher upper limits."),
    "tsh": Reference("tsh", "MEDLINEPLUS_TSH", "non-pregnant adults", any_sex=Interval("0.4", "4.8", "mIU/L"),
                     note="µU/mL = mIU/L. Pregnancy needs trimester-specific ranges; some labs use higher limits for older people."),
    "uric_acid": Reference("uric_acid", "MEDLINEPLUS_URIC", "adults", male=Interval("4.0", "8.6", "mg/dL"), female=Interval("3.0", "7.1", "mg/dL")),
}

# Where docs/09 §5.6 printed a different number than the verified source (source value is used).
DOCS_DIFFERENCE: dict[str, str] = {
    "hemoglobin": "docs/09: M 13–17, F 12–16 g/dL; WHO 2024 gives lower cut-offs only (MedlinePlus M 13–18).",
    "wbc": "docs/09: 4,000–11,000; MedlinePlus: 4,500–11,000.",
    "creatinine": "docs/09: 0.7–1.3 (male range only); MedlinePlus female 0.5–0.95.",
    "glucose_fasting": "docs/09: 70–100; MedlinePlus 70–99 and ADA counts 100 as above normal.",
    "alt": "docs/09: 7–56 U/L — not found in a fetched source; MedlinePlus 4–36.",
    "tsh": "docs/09: 0.4–4.0; MedlinePlus 0.4–4.8.",
    "uric_acid": "docs/09: 3.4–7.0 (not sex-specific); MedlinePlus F 3.0–7.1, M 4.0–8.6.",
    "troponin_i": "docs/09: <0.04 ng/mL — no universal limit exists (Fifth UDMI 2026); not in this table.",
}

# Equivalent unit spellings for the same quantity (no numeric conversion is ever applied).
_SAME_QUANTITY = {("/cumm", "/cumm"), ("µIU/mL", "mIU/L"), ("mIU/L", "µIU/mL"), ("mIU/L", "mIU/L")}


def _cmp(value: Decimal, iv: Interval) -> str:
    if iv.low is not None and value < Decimal(iv.low):
        return "below_range"
    if iv.high is not None and (value >= Decimal(iv.high) if iv.high_exclusive else value > Decimal(iv.high)):
        return "above_range"
    # One-sided sources (WHO Hb gives only a lower cut-off; A1C only an upper one) cannot say "within":
    if iv.high is None:
        return "not_below_cutoff"
    if iv.low is None:
        return "below_upper_cutoff"
    return "within_range"


def unit_matches(reference_unit: str, printed_unit: str | None) -> bool:
    if printed_unit is None:
        return False
    return printed_unit == reference_unit or (printed_unit, reference_unit) in _SAME_QUANTITY


def evaluate(analyte_key: str, value: str | None, comparator: str | None, printed_unit: str | None) -> dict | None:
    """Compare an exact decimal value with the sourced interval. Returns None if the test is not in the
    table. Conservative: unit mismatch, a comparator result or sex disagreement → `not_comparable`."""
    ref = REFERENCES.get(analyte_key)
    if ref is None:
        return None
    unit = (ref.any_sex or ref.male or ref.female).unit  # type: ignore[union-attr]
    out = {"source_id": ref.source_id, "citation": LAB_SOURCES[ref.source_id], "population": ref.population, "unit": unit,
           "status": "not_comparable", "band": None, "note": ref.note}
    if value is None or comparator is not None or not unit_matches(unit, printed_unit):
        return out
    v = Decimal(value)
    if ref.any_sex:
        out["status"] = _cmp(v, ref.any_sex)
    else:
        statuses = {_cmp(v, iv) for iv in (ref.male, ref.female) if iv}
        out["status"] = statuses.pop() if len(statuses) == 1 else "not_comparable"
    for band in ref.bands:
        if (band.low is None or v >= Decimal(band.low)) and (band.high is None or v < Decimal(band.high)):
            out["band"] = {"label": band.label, "source_id": band.source_id, "citation": LAB_SOURCES[band.source_id]}
    return out
