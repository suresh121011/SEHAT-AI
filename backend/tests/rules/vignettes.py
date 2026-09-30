"""Synthetic vignettes only. No real patient data."""

from copy import deepcopy
from typing import Any

from app.rules import TriageInput, TriageResult, evaluate_triage

NORMAL_VITALS: dict[str, Any] = {
    "resp_rate": 16,
    "spo2": 98,
    "on_supplemental_oxygen": False,
    "pulse": 78,
    "sbp": 124,
    "dbp": 80,
    "temp_c": 36.8,
    "consciousness": "A",
}


def case(scenario: str = "opd", *, vitals: dict | None = None, **overrides: Any) -> dict[str, Any]:
    """A complete, screened, normal adult case; override anything."""
    data: dict[str, Any] = {
        "scenario": scenario,
        "age_years": 40,
        "red_flag_screen_completed": True,
        "vitals": {**deepcopy(NORMAL_VITALS), **(vitals or {})},
    }
    data.update(overrides)
    return data


def run(data: dict[str, Any]) -> TriageResult:
    return evaluate_triage(TriageInput(**data))


def rule_ids(result: TriageResult) -> set[str]:
    return {t.rule_id for t in result.triggered_rules}
