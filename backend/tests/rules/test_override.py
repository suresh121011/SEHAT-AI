"""LLM boundary: suggestions may raise urgency, never lower it."""

import pytest

from app.rules import Urgency, enforce_raise_only
from tests.rules.vignettes import case, run

RED_CASE = case(vitals={"spo2": 85})
YELLOW_CASE = case(vitals={"consciousness": "C"})
GREEN_CASE = case()


@pytest.mark.parametrize(
    "data,suggested,final,applied",
    [
        (RED_CASE, Urgency.GREEN, Urgency.RED, True),
        (RED_CASE, Urgency.YELLOW, Urgency.RED, True),
        (RED_CASE, Urgency.RED, Urgency.RED, False),
        (YELLOW_CASE, Urgency.GREEN, Urgency.YELLOW, True),
        (YELLOW_CASE, Urgency.RED, Urgency.RED, True),
        (GREEN_CASE, Urgency.GREEN, Urgency.GREEN, False),
        (GREEN_CASE, Urgency.YELLOW, Urgency.YELLOW, True),
        (GREEN_CASE, Urgency.RED, Urgency.RED, True),
        (GREEN_CASE, None, Urgency.GREEN, False),
    ],
)
def test_raise_only(data, suggested, final, applied):
    result = run(data)
    before = result.model_copy(deep=True)
    out = enforce_raise_only(result, suggested)
    assert out.final_urgency == final
    assert out.override_applied is applied
    assert out.deterministic_urgency == result.urgency
    assert result == before  # deterministic result is never mutated


def test_downgrade_reason_recorded():
    out = enforce_raise_only(run(RED_CASE), Urgency.GREEN)
    assert out.override_reason == "LLM cannot downgrade deterministic safety classification"


def test_insufficient_data_floor_cannot_be_lowered():
    out = enforce_raise_only(run({"scenario": "opd"}), Urgency.GREEN)
    assert out.final_urgency == Urgency.YELLOW
