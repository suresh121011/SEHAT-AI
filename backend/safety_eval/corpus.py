"""Load and check the evaluation corpus (docs/25 §3). Plain data plus the scenario bases the vignettes start from.

A vignette's `input` is applied on top of its scenario's base: a complete, screened, normal case that earns GREEN
(pinned by tests/safety_eval/test_corpus.py). Dictionaries merge; any other value, including null, replaces.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.rules import TriageInput

DATA_DIR = Path(__file__).resolve().parent

NORMAL_VITALS: dict[str, Any] = {
    "resp_rate": 16, "spo2": 98, "on_supplemental_oxygen": False, "pulse": 78, "sbp": 124, "dbp": 80, "temp_c": 36.8,
    "consciousness": "A",
}

SCENARIO_BASE: dict[str, dict[str, Any]] = {
    "opd": {},
    "maternal": {"age_years": 26, "pregnant": True, "trimester": 2, "maternal": {"danger_sign_screen_completed": True}},
    "chronic_ncd": {"age_years": 52, "chronic_ncd": {"bp_readings": [{"sbp": 124, "dbp": 80}, {"sbp": 126, "dbp": 82}]}},
    "health_camp": {"health_camp": {"cbac_score": 2}},
    "campus_fever": {"age_years": 20, "campus_fever": {"cough": False, "onset_days": 1}},
    "occupational": {"occupational": {"sts_db_avg_2_3_4khz": 2.0}},
    "referral": {"referral": {"escort_arranged": True, "transport_arranged": True, "identity_confirmed": True, "consent_taken": True}},
}

Level = Literal["RED", "YELLOW", "GREEN"]


def merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def base_case(scenario: str) -> dict[str, Any]:
    common = {"scenario": scenario, "age_years": 40, "red_flag_screen_completed": True, "vitals": deepcopy(NORMAL_VITALS)}
    return merge(common, SCENARIO_BASE[scenario])


def build_input(scenario: str, overrides: dict[str, Any]) -> TriageInput:
    return TriageInput(**merge(base_case(scenario), overrides))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Expect(_Strict):
    urgency: Level
    fired: list[str] = Field(default_factory=list)
    not_fired: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    advisories: list[str] = Field(default_factory=list)


class Vignette(_Strict):
    id: str = Field(pattern=r"^[a-z]\d{2}$")
    category: str
    scenario: str
    title: str = Field(min_length=3)
    input: dict[str, Any]
    expect: Expect
    basis: list[tuple[str, str]] = Field(min_length=1)


class Boundary(_Strict):
    id: str = Field(pattern=r"^b\d{2}$")
    rule: str
    scenario: str = "opd"
    basis: tuple[str, str]
    probes: list[tuple[dict[str, Any], bool]] = Field(min_length=2)


class OpenQuestion(_Strict):
    """A case the specification leaves GREEN that needs a clinical decision. Deliberately unlabelled."""

    id: str = Field(pattern=r"^oq\d+$")
    scenario: str
    input: dict[str, Any]
    question: str = Field(min_length=20)


@dataclass(frozen=True)
class VignetteCorpus:
    vignettes: list[Vignette]
    boundaries: list[Boundary]
    labelled_on: str
    open_questions: list[OpenQuestion] = field(default_factory=list)


@dataclass(frozen=True)
class TextCase:
    id: str
    text: str
    category: str = ""
    expected: tuple[str, ...] = field(default=())  # PII: the raw fragment that must not survive, or values to keep


@dataclass(frozen=True)
class TextCorpus:
    output_block: list[TextCase]
    output_pass: list[TextCase]
    input_flag: list[TextCase]
    input_pass: list[TextCase]
    pii_identifier: list[TextCase]
    pii_clinical: list[TextCase]
    labelled_on: str


@lru_cache(maxsize=1)
def load_vignettes() -> VignetteCorpus:
    raw = json.loads((DATA_DIR / "vignettes.json").read_text(encoding="utf-8"))
    return VignetteCorpus(
        vignettes=[Vignette(**v) for v in raw["vignettes"]],
        boundaries=[Boundary(**b) for b in raw["boundaries"]],
        labelled_on=raw["labelled_on"],
        open_questions=[OpenQuestion(**q) for q in raw.get("open_questions", [])],
    )


@lru_cache(maxsize=1)
def load_text_cases() -> TextCorpus:
    raw = json.loads((DATA_DIR / "text_cases.json").read_text(encoding="utf-8"))
    og, ir, pii = raw["output_guard"], raw["input_rail"], raw["pii"]
    return TextCorpus(
        output_block=[TextCase(i, t, c) for i, c, t in og["block"]],
        output_pass=[TextCase(i, t) for i, t in og["pass"]],
        input_flag=[TextCase(i, t) for i, t in ir["flag"]],
        input_pass=[TextCase(i, t) for i, t in ir["pass"]],
        pii_identifier=[TextCase(i, t, c, (raw_fragment,)) for i, c, t, raw_fragment in pii["identifier"]],
        pii_clinical=[TextCase(i, t, "clinical", tuple(values)) for i, t, values in pii["clinical"]],
        labelled_on=raw["labelled_on"],
    )
