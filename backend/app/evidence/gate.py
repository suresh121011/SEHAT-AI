"""The evidence gate (docs/23): decides whether a passage may be shown as checked, shown with a warning, or withheld.
Deterministic: no model, no web search, no ranking. Corrective in the CRAG sense, but it abstains instead of searching.

States
- VERIFIED: the text's SHA-256 matches the stored hash, it comes from the rule's own source, it does not read like
  instructions, its licence allows quoting, it contains every threshold number of the rule, and a named reviewer checked
  it against the source on a recorded date. That reviewer must be on the owner's list (reviewers.json).
- UNCHECKED: shown with a warning: the licence allows quoting and the text is intact, but no reviewer has checked it,
  its reviewer is not on the owner's list, or the rule's threshold numbers are not all in it.
- LOCATOR_ONLY: no text yet, or the licence to quote is not confirmed: the citation and the location as cited are shown.
- WITHHELD: the text was changed after hashing, comes from another source, or the guard blocks it (instructions,
  an override or urgency-lowering wording, diagnostic or prescriptive wording, an identifier): nothing shown.

The hash detects drift (a text edited without re-hashing), not deliberate tampering: whoever edits the text can
re-hash it. Review of every change to passages.jsonl is the control against that; this file, the corpus and the reviewer list are
protected files in the agent harness (.claude/hooks/sehat_policy.py), so an agent asks the owner before changing them.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.ai import guard
from app.evidence.corpus import Passage

_QUOTABLE = ("quote_permitted", "project_text")
REVIEWERS_PATH = Path(__file__).with_name("reviewers.json")


class _Reviewer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=120)
    added: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class _ReviewerList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    note: str = Field(min_length=1, max_length=600)
    reviewers: list[_Reviewer] = Field(default_factory=list, max_length=100)


@lru_cache(maxsize=1)
def reviewers(path: Path = REVIEWERS_PATH) -> frozenset[str]:
    """The names on the owner's reviewer list. Only the owner edits that file."""
    names = [r.name for r in _ReviewerList(**json.loads(path.read_text(encoding="utf-8"))).reviewers]
    if len(names) != len(set(names)):
        raise ValueError("a reviewer is listed twice")
    return frozenset(names)


@dataclass(frozen=True)
class Assessment:
    state: str
    reasons: list[str] = field(default_factory=list)


def _contains_number(text: str, number: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", text) is not None


def assess(p: Passage, rule_source_id: str, allowed: frozenset[str] | None = None) -> Assessment:
    """`allowed` is the reviewer list; tests pass their own, everything else reads the owner's."""
    allowed = reviewers() if allowed is None else allowed
    if p.source_id != rule_source_id:
        return Assessment("WITHHELD", ["source_mismatch"])
    if p.text is None:
        return Assessment("LOCATOR_ONLY", ["no_passage_text"])
    if p.sha256 is None or hashlib.sha256(p.text.encode("utf-8")).hexdigest() != p.sha256:
        return Assessment("WITHHELD", ["hash_mismatch"])
    # Quoted protocol text goes through the same checks as model input and output: it sits beside a flag, and the
    # assistant may read it later (Phase 5). A genuine passage the guard blocks stays a location only.
    if code := guard.check_input(p.text) or guard.check_output(p.text):
        return Assessment("WITHHELD", ["instruction_like_text" if code == "instruction_text" else "guard_blocked"])
    if p.licence.status not in _QUOTABLE:
        return Assessment("LOCATOR_ONLY", ["licence_not_confirmed"])
    reasons = []
    if any(not _contains_number(p.text, a) for a in p.anchors):
        reasons.append("threshold_not_in_passage")
    if not p.reviewer or not p.reviewed_at:
        reasons.append("not_reviewed")
    elif p.reviewer not in allowed:
        reasons.append("reviewer_not_listed")
    return Assessment("UNCHECKED", reasons) if reasons else Assessment("VERIFIED")


_MESSAGES = {
    "no_passage_text": "The exact passage has not been added and checked yet. Read it in the source at this location.",
    "licence_not_confirmed": "Kept as a location only until the licence to quote this source is confirmed.",
    "threshold_not_in_passage": "It does not contain every threshold number of this rule.",
    "not_reviewed": "It has not been checked against the source by a reviewer.",
    "reviewer_not_listed": "Its reviewer is not on the owner's list of reviewers.",
}


def view(p: Passage, rule_source_id: str, allowed: frozenset[str] | None = None) -> dict:
    """What the screen may show for one passage. Text only for VERIFIED and UNCHECKED."""
    a = assess(p, rule_source_id, allowed)
    if a.state == "VERIFIED":
        message = f"Checked against the source by {p.reviewer} on {p.reviewed_at}."
    elif a.state == "UNCHECKED":
        message = "Shown for reference, not yet checked: " + " ".join(_MESSAGES[r] for r in a.reasons)
    elif a.state == "WITHHELD":
        message = "Passage withheld: it failed an integrity check. The citation and location are shown instead."
    else:
        message = " ".join(_MESSAGES[r] for r in a.reasons)
    shown = a.state in ("VERIFIED", "UNCHECKED")
    # A passage from another source has no location in this rule's source.
    locator = None if "source_mismatch" in a.reasons else p.locator
    return {"passage_id": p.passage_id, "state": a.state, "reasons": a.reasons, "locator": locator,
            "text": p.text if shown else None, "reviewer": p.reviewer if shown else None,
            "reviewed_at": p.reviewed_at if shown else None, "message": message}
