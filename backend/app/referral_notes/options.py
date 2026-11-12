"""Closed lists, the status chain and the overdue rule for referral closure tracking (docs/21). Pure: no I/O.

Every value a person can choose is a code from a closed list: no free text reaches a referral (tests/privacy/
test_boundary.py). Labels are plain, non-diagnostic words (checked against app.ai.guard in the tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

# Architecture §16: "Referred → Overdue: no update in 48h" and "InTransit → Overdue: no arrival confirmation".
# A project policy number taken from the architecture document, not a clinical standard.
OVERDUE_HOURS = 48

Status = Literal["referred", "in_transit", "reached", "seen", "outcome_recorded", "not_reached"]
NextStatus = Literal["in_transit", "reached", "seen", "outcome_recorded", "not_reached"]
STATUS_LABELS: dict[str, str] = {
    "referred": "Referred",
    "in_transit": "On the way",
    "reached": "Reached the referral facility",
    "seen": "Seen at the referral facility",
    "outcome_recorded": "Outcome recorded",
    "not_reached": "Did not reach the referral facility",
}
# Forward only. "not_reached" closes a referral the patient could not complete; the doctor can refer again.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "referred": ("in_transit", "reached", "not_reached"),
    "in_transit": ("reached", "not_reached"),
    "reached": ("seen",),
    "seen": ("outcome_recorded",),
    "outcome_recorded": (),
    "not_reached": (),
}
TERMINAL = frozenset({"outcome_recorded", "not_reached"})
OVERDUE_ELIGIBLE = frozenset({"referred", "in_transit"})  # once the patient has arrived, the closure gap is closed

ReasonCode = Literal["emergency_care", "specialist_assessment", "investigations", "maternal_newborn_care", "admission_observation"]
REASONS: dict[str, str] = {
    "emergency_care": "Emergency care at a higher facility",
    "specialist_assessment": "Assessment by a specialist",
    "investigations": "Tests not available at this facility",
    "maternal_newborn_care": "Maternal or newborn care",
    "admission_observation": "Admission or observation",
}

TransportMode = Literal["108_ambulance", "own_vehicle", "public_transport"]
TRANSPORT: dict[str, str] = {
    "108_ambulance": "108 ambulance",  # SEHAT does not call the ambulance; staff call 108 themselves
    "own_vehicle": "Own vehicle",
    "public_transport": "Public transport",
}

InsuranceScheme = Literal["pmjay", "gjay", "none", "unknown"]
INSURANCE: dict[str, str] = {
    "pmjay": "AB PM-JAY (as stated by the patient)",
    "gjay": "GJAY, Odisha (as stated by the patient)",
    "none": "None",
    "unknown": "Not known",
}

OutcomeCode = Literal["admitted", "discharged", "referred_onward"]
OUTCOMES: dict[str, str] = {
    "admitted": "Admitted",
    "discharged": "Treated and sent home",
    "referred_onward": "Referred onward",
}

BarrierCode = Literal["transport", "cost", "escort", "declined", "not_contactable"]
BARRIERS: dict[str, str] = {
    "transport": "No transport",
    "cost": "Cost",
    "escort": "No one to go with the patient",
    "declined": "Patient chose not to go",
    "not_contactable": "Could not contact the patient",
}

FACILITY_CODE = re.compile(r"^[A-Z0-9-]{3,32}$")


@dataclass(frozen=True)
class Facility:
    code: str
    name: str
    level: str  # PHC | CHC | SDH | DH | MCH
    district: str


# Demo directory: synthetic codes with generic names by facility type and town. Replace it with the state's facility
# registry (for example the ABDM Health Facility Registry) before any real use.
FACILITIES: dict[str, Facility] = {f.code: f for f in (
    Facility("PHC-KHURDA-01", "Primary Health Centre, Khurda", "PHC", "Khurda"),
    Facility("CHC-JATNI-01", "Community Health Centre, Jatni", "CHC", "Khurda"),
    Facility("DH-BBSR-01", "District Hospital, Bhubaneswar", "DH", "Khurda"),
    Facility("MCH-BBSR-01", "Medical College Hospital, Bhubaneswar", "MCH", "Khurda"),
)}
DestinationCode = Literal["CHC-JATNI-01", "DH-BBSR-01", "MCH-BBSR-01"]
assert set(get_args(DestinationCode)) == {c for c, f in FACILITIES.items() if f.level != "PHC"}


def facility_view(code: str) -> dict:
    f = FACILITIES.get(code)
    if f is None:  # a facility outside the demo directory is shown by its code
        return {"code": code, "name": code, "level": None, "district": None}
    return {"code": f.code, "name": f.name, "level": f.level, "district": f.district}


def destinations() -> list[dict]:
    return [facility_view(c) for c in get_args(DestinationCode)]


def labelled(labels: dict[str, str]) -> list[dict]:
    return [{"code": k, "label": v} for k, v in labels.items()]


def is_overdue(status: str, last_update: datetime, now: datetime) -> bool:
    return status in OVERDUE_ELIGIBLE and (now - last_update).total_seconds() >= OVERDUE_HOURS * 3600


def hours_since(last_update: datetime, now: datetime) -> int:
    return max(0, int((now - last_update).total_seconds() // 3600))


def detail_label(status: str, code: str | None) -> str | None:
    if code is None:
        return None
    return (OUTCOMES if status == "outcome_recorded" else BARRIERS).get(code)
