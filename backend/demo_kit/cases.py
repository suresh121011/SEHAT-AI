"""The synthetic cases the demo database starts with (docs/07 "Demo data"). Each states the urgency and rules the
engine must give it; tests/demo_kit pins both against the real engine, so a rule change shows up here.

Made-up teaching cases only: no names, numbers or places that identify anyone.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

FACILITY = "PHC-KHURDA-01"  # used when the demo accounts are not restricted to a facility

_NORMAL = {"resp_rate": 16, "spo2": 98, "on_supplemental_oxygen": False, "pulse": 78, "sbp": 122, "dbp": 80, "temp_c": 36.8,
           "consciousness": "A"}

# The docs/07 Beat 4 patient: 28 y, fever 3 days, headache, severe abdominal pain. Pinned by tests/rules/test_demo_vignette.py.
DEMO_PATIENT: dict[str, Any] = {
    "scenario": "opd", "age_years": 28, "red_flag_screen_completed": True, "red_flags_present": ["severe_pain"],
    "suspected_infection": True,
    "vitals": {"resp_rate": 20, "spo2": 97, "on_supplemental_oxygen": False, "pulse": 96, "sbp": 118, "dbp": 76, "temp_c": 38.9,
               "consciousness": "A"},
}


def _opd(age: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"scenario": "opd", "age_years": age, "red_flag_screen_completed": True, "red_flags_present": [],
                            "suspected_infection": False, "vitals": deepcopy(_NORMAL)}
    vitals = overrides.pop("vitals", {})
    data.update(overrides)
    data["vitals"].update(vitals)
    return data


@dataclass(frozen=True)
class Referral:
    to_facility: str = "DH-BBSR-01"
    reason_code: str = "emergency_care"
    transport_mode: str = "108_ambulance"
    escort_required: bool = True
    travel_minutes: int = 45
    insurance_scheme: str = "pmjay"
    statuses: tuple[str, ...] = ("in_transit",)


@dataclass(frozen=True)
class SeedCase:
    key: str
    title: str  # what the presenter calls it; never shown as a patient name
    triage: dict[str, Any]
    urgency: str
    rules: tuple[str, ...]
    sign_off: bool = False
    referral: Referral | None = None
    beat: str = ""
    notes: tuple[str, ...] = field(default=())


CASES: tuple[SeedCase, ...] = (
    SeedCase("demo_patient", "Fallback: the demo patient, already triaged", deepcopy(DEMO_PATIENT), "RED", ("ATP_RED_SEVERE_PAIN",),
             beat="Beats 4-6 if live intake fails"),
    SeedCase("safety_floor", "Same vitals, red-flag screen not done",
             {**deepcopy(DEMO_PATIENT), "red_flag_screen_completed": False, "red_flags_present": []}, "YELLOW",
             ("SAFETY_FLOOR_INSUFFICIENT_DATA",), beat="Beat 4: GREEN must be earned"),
    SeedCase("breathing", "Breathless, SpO2 86 % on air", _opd(46, vitals={"resp_rate": 28, "spo2": 86}), "RED",
             ("ATP_RED_RR", "ATP_RED_SPO2"), beat="Beat 5: the queue puts RED first"),
    SeedCase("maternal_danger_sign", "Pregnant, headache with blurred vision",
             {"scenario": "maternal", "age_years": 24, "pregnant": True, "trimester": 2, "red_flag_screen_completed": True,
              "red_flags_present": [], "suspected_infection": False, "vitals": deepcopy(_NORMAL),
              "maternal": {"danger_sign_screen_completed": True, "danger_signs": ["headache_blurred_vision"]}},
             "YELLOW", ("MAT_YELLOW_DANGER_SIGN",), beat="Beat 5: a scenario pack (MoHFW danger sign)"),
    SeedCase("routine", "Routine visit, all observations normal", _opd(34), "GREEN", (), beat="Beat 5: GREEN is earned"),
    SeedCase("referral_in_transit", "Chest pain, signed off and referred", _opd(61, red_flags_present=["chest_pain_acute_24h"]),
             "RED", ("ATP_RED_CHEST_PAIN_ACUTE_24H",), sign_off=True, referral=Referral(), beat="Beat 6: referral tracked to closure"),
)
