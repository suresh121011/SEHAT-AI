"""ATP RED criteria (ATP_2022 Supplementary Table 1): boundaries and every flag."""

import pytest

from app.rules.models import AtpFlag
from tests.rules.vignettes import case, rule_ids, run


def atp_hit(result, rule_id):
    return rule_id in rule_ids(result)


@pytest.mark.parametrize("rr,red", [(22, False), (23, True), (10, False), (9, True)])
def test_resp_rate(rr, red):
    assert atp_hit(run(case(vitals={"resp_rate": rr})), "ATP_RED_RR") is red


@pytest.mark.parametrize("spo2,red", [(90, False), (89, True)])
def test_spo2(spo2, red):
    assert atp_hit(run(case(vitals={"spo2": spo2})), "ATP_RED_SPO2") is red


@pytest.mark.parametrize(
    "pulse,temp,red",
    [(120, 36.8, False), (121, 36.8, True), (121, 38.0, True), (121, 38.1, False), (121, None, True), (50, 36.8, False), (49, 36.8, True), (49, 39.0, True)],
)
def test_pulse_without_fever(pulse, temp, red):
    # keep shock index <=1 so only the pulse rule is under test
    r = run(case(vitals={"pulse": pulse, "temp_c": temp, "sbp": 140, "dbp": 85}))
    assert atp_hit(r, "ATP_RED_PULSE") is red


@pytest.mark.parametrize("sbp,dbp,red", [(220, 100, False), (221, 100, True), (200, 110, False), (200, 111, True)])
def test_bp_high(sbp, dbp, red):
    assert atp_hit(run(case(vitals={"sbp": sbp, "dbp": dbp})), "ATP_RED_BP_HIGH") is red


@pytest.mark.parametrize("sbp,dbp,red", [(90, 60, False), (89, 60, True), (100, 59, True)])
def test_bp_low(sbp, dbp, red):
    assert atp_hit(run(case(vitals={"sbp": sbp, "dbp": dbp, "pulse": 70})), "ATP_RED_BP_LOW") is red


@pytest.mark.parametrize("pulse,sbp,red", [(100, 100, False), (101, 100, True)])
def test_shock_index(pulse, sbp, red):
    assert atp_hit(run(case(vitals={"pulse": pulse, "sbp": sbp, "dbp": 70})), "ATP_RED_SHOCK_INDEX") is red


@pytest.mark.parametrize("acvpu,red", [("A", False), ("C", False), ("V", True), ("P", True), ("U", True)])
def test_altered_sensorium(acvpu, red):
    assert atp_hit(run(case(vitals={"consciousness": acvpu})), "ATP_RED_SENSORIUM") is red


@pytest.mark.parametrize("temp,red", [(39.0, False), (39.1, True)])
def test_fever_over_39(temp, red):
    assert atp_hit(run(case(vitals={"temp_c": temp})), "ATP_RED_FEVER_39") is red


@pytest.mark.parametrize("flag", list(AtpFlag))
def test_every_flag_is_red_with_explanation(flag):
    r = run(case(red_flags_present=[flag.value]))
    assert r.urgency.value == "RED"
    hit = next(t for t in r.triggered_rules if t.rule_id == f"ATP_RED_{flag.name}")
    assert hit.source_id == "ATP_2022"
    assert hit.reason
    assert hit.evidence["red_flag"].value == flag.value


def test_multiple_red_flags_all_listed():
    r = run(case(red_flags_present=["stridor", "chest_pain_acute_24h"], vitals={"spo2": 85}))
    assert r.urgency.value == "RED"
    assert {"ATP_RED_STRIDOR", "ATP_RED_CHEST_PAIN_ACUTE_24H", "ATP_RED_SPO2"} <= rule_ids(r)


def test_atp_applies_below_validated_age_but_only_to_escalate():
    r = run(case(age_years=10, red_flags_present=["active_seizure"]))
    assert r.urgency.value == "RED"
    assert r.determination == "outside_validated_population"
