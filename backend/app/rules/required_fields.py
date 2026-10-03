"""Per-scenario required information (docs/09 §6.4, architecture §12). Pure: no I/O.

A field counts as present only when the caller passes it as present (a human-reviewed or agreed extracted
value, or a reviewed document value). Anything else is missing and needs human review — never "normal".
OPD, maternal and chronic-NCD lists follow the architecture document; the other scenarios use the core
items the rules engine needs to earn GREEN (a complaint plus vitals).
"""

from dataclasses import dataclass

from app.rules.models import Scenario


@dataclass(frozen=True)
class Required:
    key: str
    label: str
    satisfied_by: tuple[str, ...]  # extraction field keys; a trailing ":" matches a prefix
    danger_sign: bool = False
    priority: int = 1  # higher first


_CORE = (
    Required("chief_complaint", "Chief complaint", ("chief_complaint",), priority=3),
    Required("spo2", "SpO2", ("spo2",), priority=2),
    Required("bp", "Blood pressure", ("bp",), priority=2),
    Required("pulse", "Pulse", ("pulse",), priority=2),
    Required("resp_rate", "Respiratory rate", ("resp_rate",), priority=2),
    Required("temp", "Temperature", ("temp",), priority=2),
)

SCENARIO_REQUIRED: dict[Scenario, tuple[Required, ...]] = {
    Scenario.OPD: (
        Required("chief_complaint", "Chief complaint", ("chief_complaint",), priority=3),
        Required("duration", "Duration", ("duration",)),
        Required("severity", "Severity (pain score)", ("pain_severity",)),
        Required("spo2", "SpO2", ("spo2",), priority=2),
        Required("bp", "Blood pressure", ("bp",), priority=2),
        Required("pulse", "Pulse", ("pulse",), priority=2),
    ),
    Scenario.MATERNAL: (
        Required("danger_signs", "Pregnancy danger-sign screen", ("maternal_danger_screen",), danger_sign=True, priority=5),
        Required("lmp", "Last menstrual period", ("lmp",)),
        Required("edd", "Expected date of delivery", ("edd",)),
        Required("gravida_parity", "Gravida / parity", ("gravida_parity",)),
        Required("hb", "Haemoglobin", ("hb", "ocr:hb"), priority=2),
        Required("bp", "Blood pressure", ("bp",), priority=2),
    ),
    Scenario.CHRONIC_NCD: (
        Required("bp_reading_1", "Blood pressure (reading 1)", ("bp",), priority=2),
        Required("bp_reading_2", "Blood pressure (reading 2)", ("bp#2",), priority=2),
        Required("blood_sugar", "Blood sugar", ("glucose", "ocr:glucose", "ocr:fasting_glucose", "ocr:random_glucose")),
        Required("current_drugs", "Current medicines", ("medication:",)),
        Required("adherence", "Medicine adherence", ("adherence",)),
    ),
}

# Every scenario first asks the red-flag rule-out (danger signs first, architecture §12).
RED_FLAG_SCREEN = Required("red_flag_screen", "Red-flag screen", ("red_flag_screen_completed",), danger_sign=True, priority=6)


def required_for(scenario: Scenario | str) -> tuple[Required, ...]:
    s = Scenario(scenario)
    return (RED_FLAG_SCREEN,) + SCENARIO_REQUIRED.get(s, _CORE)


def missing(scenario: Scenario | str, present: set[str]) -> list[Required]:
    """Required items not present, highest priority first (danger signs first)."""

    def ok(r: Required) -> bool:
        return any(k in present or (k.endswith(":") and any(p.startswith(k) for p in present)) for k in r.satisfied_by)

    out = [r for r in required_for(scenario) if not ok(r)]
    return sorted(out, key=lambda r: (not r.danger_sign, -r.priority))
