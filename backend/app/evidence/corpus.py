"""The evidence corpus (docs/23): one or more passages per rule, from the rule's own cited source, with the location
in that source as cited (a section not yet confirmed says so). Stored as JSON lines in `passages.jsonl`, reviewed like code (`scripts/lint_evidence_corpus.py`).

A passage's `text` stays empty until someone copies the exact words from the source, records its SHA-256, confirms the
licence allows quoting, and a named reviewer checks it against the source. Until then the screen shows the citation and
location only (`app.evidence.gate`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CORPUS_PATH = Path(__file__).with_name("passages.jsonl")
LICENCE_STATUSES = ("to_check", "quote_permitted", "locator_only", "project_text")
_NUMBER = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?")

# Rules whose ids are built inside the engine (app/rules/engine.py) rather than declared as `Rule` objects.
DYNAMIC_RULES: dict[str, tuple[str, str]] = {
    "NEWS2_HIGH": ("RCP_NEWS2_2017", "NEWS2 high clinical risk (RCP Chart 2): aggregate score 7 or more"),
    "NEWS2_MEDIUM": ("RCP_NEWS2_2017", "NEWS2 medium clinical risk (RCP Chart 2): aggregate score 5 to 6"),
    "NEWS2_LOW_MEDIUM": ("RCP_NEWS2_2017", "NEWS2 low-medium clinical risk (RCP Chart 2): a single parameter scoring 3"),
    "QSOFA_POSITIVE": ("SEPSIS3_2016", "qSOFA positive screen: 2 or more of respiratory rate 22 or more, altered mentation, systolic BP 100 or less"),
    "SAFETY_FLOOR_OUTSIDE_POPULATION": ("SEHAT_POLICY", "Age below the ATP validation population: GREEN cannot be assigned"),
    "SAFETY_FLOOR_INSUFFICIENT_DATA": ("SEHAT_POLICY", "Required data missing: GREEN cannot be assigned without complete data and a completed red-flag screen"),
}
# Their threshold numbers, written out because the wording above also holds other numbers ("RCP Chart 2"). Pinned
# against the engine's own thresholds by tests/evidence/test_evidence.py.
DYNAMIC_ANCHORS: dict[str, tuple[str, ...]] = {
    "NEWS2_HIGH": ("7",),
    "NEWS2_MEDIUM": ("5", "6"),
    "NEWS2_LOW_MEDIUM": ("3",),
    "QSOFA_POSITIVE": ("2", "22", "100"),
    "SAFETY_FLOOR_OUTSIDE_POPULATION": (),
    "SAFETY_FLOOR_INSUFFICIENT_DATA": (),
}


class Licence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["to_check", "quote_permitted", "locator_only", "project_text"]
    note: str = Field(min_length=1, max_length=300)


class Passage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passage_id: str = Field(pattern=r"^[a-z0-9_]{3,80}$")
    rule_ids: list[str] = Field(min_length=1, max_length=50)
    source_id: str = Field(pattern=r"^[A-Z0-9_]{3,40}$")
    locator: str = Field(min_length=3, max_length=300)
    anchors: list[str] = Field(default_factory=list, max_length=12)
    text: str | None = Field(default=None, min_length=1, max_length=1200)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    licence: Licence
    reviewer: str | None = Field(default=None, min_length=2, max_length=120)
    reviewed_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("reviewed_at")
    @classmethod
    def _a_real_past_date(cls, value: str | None) -> str | None:
        if value is not None and date.fromisoformat(value) > date.today():
            raise ValueError("a review date cannot be in the future")
        return value  # date.fromisoformat also refuses impossible dates such as 2026-99-99


@dataclass(frozen=True)
class Corpus:
    passages: list[Passage]
    by_rule: dict[str, list[Passage]] = field(default_factory=dict)


def numbers(text: str) -> list[str]:
    """Numbers that stand on their own in a rule's wording ("SpO2 <90%" gives 90, not the 2 of SpO2)."""
    return list(dict.fromkeys(_NUMBER.findall(text)))


def expected_anchors(rule_id: str, reason: str) -> set[str]:
    """The threshold numbers a passage for this rule must contain."""
    return set(DYNAMIC_ANCHORS[rule_id]) if rule_id in DYNAMIC_ANCHORS else set(numbers(reason))


def rule_catalogue() -> dict[str, tuple[str, str]]:
    """Every rule id the engine can fire → (source id, wording). Imported lazily: app.rules never imports this package."""
    from app.rules.atp import ATP_RULES
    from app.rules.scenarios import PACKS

    static = list(ATP_RULES) + [r for p in PACKS.values() for r in p.urgency_rules]
    return {**{r.id: (r.source_id, r.reason) for r in static}, **DYNAMIC_RULES}


def parse(lines: list[str]) -> list[Passage]:
    return [Passage(**json.loads(line)) for line in lines if line.strip()]


@lru_cache(maxsize=1)
def load(path: Path = CORPUS_PATH) -> Corpus:
    passages = parse(path.read_text(encoding="utf-8").splitlines())
    by_rule: dict[str, list[Passage]] = {}
    for p in passages:
        for rule_id in p.rule_ids:
            by_rule.setdefault(rule_id, []).append(p)
    return Corpus(passages=passages, by_rule=by_rule)
