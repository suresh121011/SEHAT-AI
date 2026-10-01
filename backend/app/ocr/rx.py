"""Prescription line extraction (docs/09 5.3: "drug names and dosage patterns ('1-0-1' format)").

Input is text lines (from Chandra OCR 2 layout blocks) with their source boxes. Output keeps the printed
text of every part; nothing is completed or "corrected". A drug name is only a candidate for the RxNorm
cross-check (app/ocr/rxnorm.py) and for the reviewer — SEHAT AI never recommends or changes a medication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ocr.lexicon import DOSAGE_FORMS
from app.ocr.types import BBox

_FORM = re.compile(r"^(?:\d+[.)]\s*)?(" + "|".join(re.escape(f) for f in sorted(DOSAGE_FORMS, key=len, reverse=True)) + r")\s+", re.IGNORECASE)
_STRENGTH = re.compile(r"\b(\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|iu|%)(?:\s*/\s*\d+(?:\.\d+)?\s*(?:mg|ml|g))?)\b", re.IGNORECASE)
_PATTERN = re.compile(r"\b([0-2½](?:\s*[-–]\s*[0-2½]){2,3})\b")
_DURATION = re.compile(r"(?:x|for|×)\s*(\d+\s*(?:days?|d|weeks?|wks?|months?))\b", re.IGNORECASE)
_FREQ_WORDS = re.compile(r"\b(od|bd|bid|tds|tid|qid|hs|sos|stat|prn)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RxLine:
    text: str
    bbox: BBox
    page_index: int
    line_id: str


@dataclass
class MedCandidate:
    page_index: int
    line_text: str
    bbox: BBox
    line_id: str
    form: str | None
    drug_raw: str
    strength_raw: str | None
    dosage_pattern: str | None
    frequency_raw: str | None
    duration_raw: str | None
    flags: list[str] = field(default_factory=list)


def extract_rx(lines: list[RxLine]) -> list[MedCandidate]:
    out: list[MedCandidate] = []
    for ln in lines:
        text = re.sub(r"\s+", " ", ln.text).strip()
        m_form = _FORM.match(text)
        has_pattern = _PATTERN.search(text)
        if not m_form and not has_pattern:
            continue  # header, patient details, advice lines: not a medication line
        rest = text[m_form.end():] if m_form else re.sub(r"^\d+[.)]\s*", "", text)
        s = _STRENGTH.search(rest)
        p = _PATTERN.search(rest)
        d = _DURATION.search(rest)
        f = _FREQ_WORDS.search(rest)
        cut = min([m.start() for m in (s, p, d, f) if m] + [len(rest)])
        drug = rest[:cut].strip(" -–,.")
        flags = []
        if not drug:
            flags.append("drug_name_missing")
        if not (p or f):
            flags.append("dosage_pattern_missing")
        if p and "½" in p.group(1):
            flags.append("fractional_dose")
        out.append(MedCandidate(
            page_index=ln.page_index, line_text=ln.text, bbox=ln.bbox, line_id=ln.line_id,
            form=m_form.group(1).rstrip(".").lower() if m_form else None, drug_raw=drug,
            strength_raw=s.group(1) if s else None, dosage_pattern=re.sub(r"\s+", "", p.group(1)).replace("–", "-") if p else None,
            frequency_raw=f.group(1) if f else None, duration_raw=d.group(1) if d else None, flags=flags,
        ))
    return out
