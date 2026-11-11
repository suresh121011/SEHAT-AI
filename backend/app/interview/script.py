"""The interview script (docs/plans/scripted-voice-interviewer.md §3, owner decision I3). Pure: no I/O, no model.

The questions are the fixed English question bank (app.ai.question_bank) for the information the latest triage form
is still missing (app.rules.required_fields.missing: danger signs first). At most 5 patient questions and, counted
apart, at most 5 health-worker tasks, so missing measurements never crowd out the questions. The script depends only on the
triage form, not on interview answers, so it does not shift while the interview is being done.

Two audiences:
- `patient`: questions the patient answers (main problem, duration, severity, pregnancy history, medicines). Only these
  may be read aloud and answered in the interview.
- `worker`: tasks for the health worker: measurements, the red-flag and danger-sign screens, lab availability. They
  are shown, never spoken and never answered here: measurements and screens are recorded on the triage form, so the
  interview can never complete a red-flag screen or supply a vital sign.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.ai.question_bank import MAX_QUESTIONS, QUESTIONS
from app.rules.models import TriageInput
from app.rules.required_fields import missing

SCRIPT_VERSION = "interview-script-2026-11-11.2"  # .2: patient questions and health-worker tasks are limited separately
LANGUAGE = "en"  # owner decision I1: English only until hi/or wording is reviewed (docs/13)

# Keys answered by the health worker (measured, screened or looked up), never asked of the patient here.
WORKER_KEYS = frozenset({"spo2", "bp", "bp_reading_1", "bp_reading_2", "pulse", "resp_rate", "temp",
                         "red_flag_screen", "danger_signs", "hb", "blood_sugar"})
# Options that record "not known": stored as given, but the information stays missing (never read as a negative).
UNKNOWN_OPTIONS = frozenset({"not sure", "not checked yet"})
TEXT_MAX = 500


@dataclass(frozen=True)
class Question:
    key: str
    label: str
    question_text: str
    kind: Literal["choice", "text"]
    options: tuple[str, ...]
    audience: Literal["patient", "worker"]
    is_danger_sign: bool

    @property
    def answerable(self) -> bool:
        return self.audience == "patient"


def present_from_triage(inp: TriageInput | None) -> set[str]:
    """Extraction-style keys the triage form already supplies. The red-flag screen counts only when the form says it
    was completed; nothing else marks a screen done."""
    if inp is None:
        return set()
    v = inp.vitals
    present = {k for k, val in (("spo2", v.spo2), ("pulse", v.pulse), ("resp_rate", v.resp_rate), ("temp", v.temp_c)) if val is not None}
    if v.sbp is not None and v.dbp is not None:
        present.add("bp")
    if inp.red_flag_screen_completed:
        present.add("red_flag_screen_completed")
    return present


def build(scenario: str, triage_input: TriageInput | None) -> list[Question]:
    out = []
    left = {"patient": MAX_QUESTIONS, "worker": MAX_QUESTIONS}  # two limits, so one audience never uses the other's places
    for r in missing(scenario, present_from_triage(triage_input)):
        q = QUESTIONS.get(r.key)
        if q is None:
            continue
        audience = "worker" if r.key in WORKER_KEYS else "patient"
        if not left[audience]:
            continue
        left[audience] -= 1
        options = tuple(q["response_options"])
        out.append(Question(key=r.key, label=r.label, question_text=q["question_text"], kind="choice" if options else "text",
                            options=options, audience=audience, is_danger_sign=r.danger_sign))
    return out


def is_unknown(q: Question, choice: str | None) -> bool:
    return q.kind == "choice" and choice in UNKNOWN_OPTIONS
