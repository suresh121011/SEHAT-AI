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
_FREQ_PHRASE = re.compile(r"\b(\d+\s*times?\s*(?:a|per|/)\s*day|once\s+(?:a\s+)?daily|twice\s+(?:a\s+)?daily|thrice\s+(?:a\s+)?daily)\b", re.IGNORECASE)
# Handwritten Indian prescriptions number their items ("① Acyclovir …", "(2) …", "2. …"), often after "Plan →"
# or "Rx", and often write the form after the name ("Sofradex ointment"). Seen 2026-10-02 on a real-world-style
# sample; before this, such lines were dropped before the reviewer could see them.
_ITEM = re.compile(r"^(?:(?:plan|rx|adv(?:ice)?)\b\s*[:\-–>→.]*\s*)?(?:[\u2460-\u2473]|\(\d{1,2}\)|\d{1,2}[.)](?=\s|[A-Za-z]))\s*", re.IGNORECASE)
_FORM_AFTER = re.compile(r"\b(ointment|oint|cream|gel|lotion|drops|syrup|tablets?|capsules?|injection|suspension|inhaler)\b\.?", re.IGNORECASE)
_CONTINUATION = re.compile(r"^[×x*]?\s*(\d+\s*(?:days?|weeks?|wks?|months?))\s*\.?$", re.IGNORECASE)


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
    prev_line_id: str | None = None
    for ln in lines:
        text = re.sub(r"\s+", " ", ln.text).strip()
        cont = _CONTINUATION.match(text)
        if cont and out and prev_line_id == out[-1].line_id and out[-1].page_index == ln.page_index:
            # "× 7 DAYS" on the next line belongs to the medication above it (kept as printed, both lines shown)
            if out[-1].duration_raw is None:
                out[-1].duration_raw = cont.group(1)
            out[-1].line_text += "\n" + ln.text
            out[-1].line_id = ln.line_id
            prev_line_id = ln.line_id
            continue
        prev_line_id = ln.line_id
        m_form = _FORM.match(text)
        item = None if m_form else _ITEM.match(text)
        after = text[item.end():] if item else text
        has_pattern = _PATTERN.search(after)
        if not m_form and not has_pattern:
            signals = [r.search(after) for r in (_STRENGTH, _FREQ_WORDS, _FREQ_PHRASE, _DURATION, _FORM_AFTER)]
            form_after = signals[4]
            # A numbered item, or a "<name> <form>" line, still needs a dosing signal to count as a medication
            if not ((item and any(signals)) or (form_after and any(signals[:4]))):
                continue  # header, patient details, findings, advice lines: not a medication line
        rest = text[m_form.end():] if m_form else (after if item else re.sub(r"^\d+[.)]\s*", "", text))
        s = _STRENGTH.search(rest)
        p = _PATTERN.search(rest)
        d = _DURATION.search(rest)
        f = _FREQ_WORDS.search(rest)
        fp = _FREQ_PHRASE.search(rest)
        fa = None if m_form else _FORM_AFTER.search(rest)
        cut = min([m.start() for m in (s, p, d, f, fp, fa) if m] + [len(rest)])
        drug = rest[:cut].strip(" -–,.")
        flags = []
        if not drug:
            flags.append("drug_name_missing")
        if not (p or f or fp):
            flags.append("dosage_pattern_missing")
        if p and "½" in p.group(1):
            flags.append("fractional_dose")
        form = m_form.group(1).rstrip(".").lower() if m_form else (fa.group(1).lower() if fa and fa.start() == cut else None)
        out.append(MedCandidate(
            page_index=ln.page_index, line_text=ln.text, bbox=ln.bbox, line_id=ln.line_id,
            form=form, drug_raw=drug,
            strength_raw=s.group(1) if s else None, dosage_pattern=re.sub(r"\s+", "", p.group(1)).replace("–", "-") if p else None,
            frequency_raw=(f.group(1) if f else fp.group(1) if fp else None), duration_raw=d.group(1) if d else None, flags=flags,
        ))
    return out
