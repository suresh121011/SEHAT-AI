"""Upload classifier for the two document pipelines (docs/18 §3). Pure, model-free and deterministic.

Policy (user decision, docs/18): the `document_type` the uploader chose is AUTHORITATIVE. `classify_upload()`
only produces a cross-check hint from the file's magic bytes and filename tokens. A disagreement is stored as
`classifier_mismatch` and shown to the reviewer; an upload is never rerouted silently.

Filename matching is on whole tokens (the name is lower-cased and split on anything that is not a letter or
digit), so "ct" matches "ct_scan.png" but never the "ct" inside "doctor_note.jpg". The filename is used only
here, in memory: it is never stored or audited (it may contain a patient name).
"""

from __future__ import annotations

import re
from typing import Literal

from app.ocr import files

ImageClass = Literal["lab_report", "prescription", "discharge_summary", "chest_xray", "ecg_strip", "ct_report_image", "wound_photo", "skin_lesion"]

TEXT_TYPES: tuple[str, ...] = ("lab_report", "prescription", "discharge_summary")
MEDGEMMA_TYPES: tuple[str, ...] = ("chest_xray", "ecg_strip", "ct_report_image", "wound_photo", "skin_lesion")
ALL_TYPES: tuple[str, ...] = TEXT_TYPES + MEDGEMMA_TYPES

_SPLIT = re.compile(r"[^a-z0-9]+")
_EXT = re.compile(r"\.[a-z0-9]{1,5}$")

# Ordered rules: the first rule with a matching token wins. Single tokens, or adjacent token pairs ("x ray").
_RULES: tuple[tuple[str, frozenset[str], tuple[tuple[str, str], ...]], ...] = (
    ("ecg_strip", frozenset({"ecg", "ekg", "electrocardiogram", "electrocardiograph", "12lead"}), (("12", "lead"),)),
    ("chest_xray", frozenset({"xray", "cxr", "radiograph", "radiography", "roentgen"}), (("x", "ray"),)),
    ("ct_report_image", frozenset({"ct", "ctscan", "cect", "ncct", "hrct", "tomography"}), (("cat", "scan"),)),
    ("wound_photo", frozenset({"wound", "ulcer", "burn", "laceration", "abrasion", "cut", "bedsore"}), ()),
    ("skin_lesion", frozenset({"skin", "lesion", "mole", "rash", "derm", "dermatology", "nevus", "naevus"}), ()),
    ("prescription", frozenset({"rx", "prescription", "prescriptions", "medicines", "medication", "medications"}), ()),
    ("discharge_summary", frozenset({"discharge"}), ()),
    ("lab_report", frozenset({"lab", "labs", "blood", "cbc", "hemogram", "haemogram", "lft", "kft", "rft", "pathology", "results"}), ()),
)


def tokens(filename: str | None) -> list[str]:
    name = (filename or "").lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = _EXT.sub("", name)
    return [t for t in _SPLIT.split(name) if t]


def classify_upload(filename: str | None, mime_type: str | None, first_bytes: bytes | None, declared_type: str | None = None) -> ImageClass | None:
    """Best-effort hint, or None when nothing matches. `declared_type` is accepted for call-site symmetry and
    deliberately ignored: the hint must be independent of what the uploader chose.

    Rule order: (1) a PDF by magic bytes is a text document → lab_report (PDFs never go to the image pipeline);
    (2..9) filename token rules above; otherwise None. The client content-type is NOT trusted for routing; it is
    used only to recognise a PDF whose bytes were not supplied."""
    del declared_type
    media = None
    if first_bytes:
        try:
            media = files.sniff(first_bytes)
        except files.DocumentInvalid:
            media = None
    if media == "application/pdf" or (media is None and (mime_type or "").lower() == "application/pdf"):
        return "lab_report"
    toks = tokens(filename)
    pairs = set(zip(toks, toks[1:]))
    for cls, singles, adjacent in _RULES:
        if singles.intersection(toks) or any(p in pairs for p in adjacent):
            return cls  # type: ignore[return-value]
    return None


def resolve(declared: str, hint: str | None) -> tuple[str, bool]:
    """(final type, mismatch). The declared type always wins; a different, non-null hint is a mismatch."""
    return declared, hint is not None and hint != declared
