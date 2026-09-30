"""Pipeline behaviour: precedence, NEWS2 mapping, completeness gate, validation, determinism."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.rules import TriageInput, evaluate_triage
from tests.rules.vignettes import case, rule_ids, run


def test_complete_normal_case_is_green():
    r = run(case())
    assert r.urgency.value == "GREEN"
    assert r.determination == "complete"
    assert r.needs_human_review is False
    assert r.triggered_rules == []
    assert r.scores.news2.total == 0


def test_docs_vignette_multi_abnormal_vitals_red():
    # docs/08 P2: {"rr": 32, "spo2": 88, "sbp": 85, "pulse": 135, "temp": 34} -> RED
    r = run(case(vitals={"resp_rate": 32, "spo2": 88, "sbp": 85, "dbp": 55, "pulse": 135, "temp_c": 34.0}))
    assert r.urgency.value == "RED"
    assert {"ATP_RED_RR", "ATP_RED_SPO2", "ATP_RED_BP_LOW", "ATP_RED_PULSE", "ATP_RED_SHOCK_INDEX", "NEWS2_HIGH"} <= rule_ids(r)


def test_news2_high_is_red_even_without_atp_trigger():
    # NEWS2 7 with no ATP criterion: RR 22 (2), SpO2 92 (2), SBP 110 (1), pulse 105 (1), 38.5 °C (1);
    # shock index 0.95, RR not >22, SpO2 not <90
    r = run(case(vitals={"resp_rate": 22, "spo2": 92, "sbp": 110, "dbp": 70, "pulse": 105, "temp_c": 38.5}))
    assert not any(t.family == "atp" for t in r.triggered_rules)
    assert r.scores.news2.total == 7
    assert r.urgency.value == "RED"


def test_news2_medium_is_yellow():
    r = run(case(vitals={"resp_rate": 22, "spo2": 95, "sbp": 105, "dbp": 70, "pulse": 95}))
    assert r.scores.news2.total == 5
    assert r.urgency.value == "YELLOW"
    assert "NEWS2_MEDIUM" in rule_ids(r)


def test_news2_single_three_is_yellow():
    r = run(case(vitals={"consciousness": "C"}))  # new confusion: NEWS2 3, not ATP RED (V/P/U)
    assert r.urgency.value == "YELLOW"
    assert "NEWS2_LOW_MEDIUM" in rule_ids(r)


def test_hypothermia_34_is_yellow_via_news2_not_invented_red():
    # docs previously expected RED from an unsourced "temp <35" rule; RCP scores <=35.0 as 3 (low-medium)
    r = run(case(vitals={"temp_c": 34.0}))
    assert r.urgency.value == "YELLOW"
    assert "NEWS2_LOW_MEDIUM" in rule_ids(r)


def test_conflicting_rules_highest_wins_and_all_are_reported():
    r = run(case(vitals={"consciousness": "C", "spo2": 88}))
    assert r.urgency.value == "RED"
    assert {"ATP_RED_SPO2", "NEWS2_HIGH"} & rule_ids(r)
    assert r.triggered_rules[0].urgency.value == "RED"  # sorted most urgent first


@pytest.mark.parametrize(
    "overrides",
    [
        {"red_flag_screen_completed": False},
        {"age_years": None},
        {"vitals": {"spo2": None}},
        {"vitals": {"consciousness": None}},
        {"vitals": {"on_supplemental_oxygen": None}},
    ],
)
def test_missing_required_data_is_never_green(overrides):
    data = case()
    for key, value in overrides.items():
        if key == "vitals":
            data["vitals"].update(value)
        else:
            data[key] = value
    r = run(data)
    assert r.urgency.value == "YELLOW"
    assert r.determination == "insufficient_data"
    assert r.needs_human_review is True
    assert r.missing_fields
    assert "SAFETY_FLOOR_INSUFFICIENT_DATA" in rule_ids(r)


def test_docs_vignette_fever_without_vitals_needs_review():
    # docs/08: missing vitals -> needs human review
    r = run({"scenario": "opd", "age_years": 30})
    assert r.urgency.value == "YELLOW"
    assert r.needs_human_review
    assert {"vitals.spo2", "vitals.sbp", "vitals.pulse"} <= set(r.missing_fields)


def test_red_is_not_reduced_by_missing_data():
    r = run({"scenario": "opd", "red_flags_present": ["chest_pain_acute_24h"]})
    assert r.urgency.value == "RED"
    assert r.determination == "insufficient_data"


def test_child_is_never_green():
    r = run(case(age_years=10))
    assert r.urgency.value == "YELLOW"
    assert r.determination == "outside_validated_population"
    assert r.scores.news2.status == "not_applicable"


def test_age_14_15_uses_atp_without_news2():
    r = run(case(age_years=15))
    assert r.urgency.value == "GREEN"
    assert r.scores.news2.status == "not_applicable"


def test_pregnancy_skips_news2():
    r = run(case(pregnant=True, trimester=2))
    assert r.scores.news2.status == "not_applicable"


@pytest.mark.parametrize(
    "bad",
    [
        {"vitals": {"spo2": 140}},
        {"vitals": {"resp_rate": -1}},
        {"vitals": {"sbp": 80, "dbp": 90}},
        {"vitals": {"consciousness": "X"}},
        {"red_flags_present": ["made_up_flag"]},
        {"scenario": "dengue"},
        {"unknown_field": 1},
        {"scenario": "opd", "maternal": {"danger_sign_screen_completed": True}},
        {"scenario": "maternal", "pregnant": False},
        # no silent coercion of clinical values
        {"vitals": {"spo2": True}},
        {"vitals": {"spo2": "95"}},
        {"vitals": {"resp_rate": 16.5}},
        {"vitals": {"temp_c": float("nan")}},
        {"red_flag_screen_completed": 1},
        {"red_flag_screen_completed": "yes"},
        {"age_years": "40"},
    ],
)
def test_invalid_input_is_rejected_not_coerced(bad):
    data = case()
    for key, value in bad.items():
        if key == "vitals":
            data["vitals"].update(value)
        else:
            data[key] = value
    with pytest.raises(ValidationError):
        TriageInput(**data)


def test_deterministic_for_same_input():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    data = TriageInput(**case(vitals={"resp_rate": 24, "pulse": 115}))
    assert evaluate_triage(data, now=now) == evaluate_triage(data, now=now)


def test_result_is_explainable_and_versioned():
    r = run(case(vitals={"spo2": 88}))
    hit = next(t for t in r.triggered_rules if t.rule_id == "ATP_RED_SPO2")
    assert hit.evidence["spo2"].value == 88
    assert hit.evidence["spo2"].threshold == "<90 %"
    assert "Supplementary Table 1" in hit.source
    assert r.engine_version and r.ruleset_version


# ── Regression tests from the independent safety review ──


@pytest.mark.parametrize("temp", [39.04, 39.05, 39.1])
def test_temperature_is_not_rounded_below_atp_threshold(temp):
    r = run(case(vitals={"temp_c": temp}))
    assert r.urgency.value == "RED"
    assert "ATP_RED_FEVER_39" in rule_ids(r)


@pytest.mark.parametrize("gcs", [3, 8, 14])
def test_alert_with_low_gcs_is_rejected_as_contradictory(gcs):
    with pytest.raises(ValidationError):
        TriageInput(**case(vitals={"consciousness": "A", "gcs": gcs}))


def test_spo2_scale_2_is_visible_in_evidence_and_advisories():
    r = run(case(vitals={"spo2": 90, "spo2_scale": 2}))
    assert r.scores.news2.components["spo2"].value == {"spo2": 90, "scale": 2}
    assert "NEWS2_SPO2_SCALE_2" in [a.code for a in r.advisories]


def test_pregnant_patient_outside_maternal_scenario_is_not_green():
    r = run(case("opd", pregnant=True, trimester=3))
    assert r.urgency.value == "YELLOW"
    assert r.needs_human_review
    assert any(f.startswith("maternal.danger_sign_screen_completed") for f in r.missing_fields)


@pytest.mark.parametrize("bad", [{"trimester": True}, {"trimester": 3.0}, {"vitals": {"spo2_scale": True}}])
def test_literal_fields_are_not_coerced(bad):
    data = case(pregnant=True)
    for key, value in bad.items():
        if key == "vitals":
            data["vitals"].update(value)
        else:
            data[key] = value
    with pytest.raises(ValidationError):
        TriageInput(**data)
