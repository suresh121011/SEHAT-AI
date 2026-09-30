"""One urgency and one advisory vignette per scenario pack. Advisories never change urgency."""

import pytest

from app.rules.models import Scenario
from app.rules.scenarios import PACKS
from tests.rules.vignettes import case, rule_ids, run


def test_every_scenario_has_a_pack():
    assert set(PACKS) == set(Scenario)


# ── OPD ──


def test_opd_chest_pain_red():
    r = run(case("opd", red_flags_present=["chest_pain_acute_24h"]))
    assert r.urgency.value == "RED"


# ── Maternal ──


def maternal(**kw):
    m = {"danger_sign_screen_completed": True, **kw.pop("maternal", {})}
    return case("maternal", pregnant=True, trimester=kw.pop("trimester", 2), maternal=m, age_years=kw.pop("age_years", 26), **kw)


def test_maternal_no_danger_signs_green():
    r = run(maternal())
    assert r.urgency.value == "GREEN"
    assert r.scores.news2.status == "not_applicable"


def test_maternal_danger_sign_yellow():
    r = run(maternal(maternal={"danger_signs": ["reduced_fetal_movement"]}))
    assert r.urgency.value == "YELLOW"
    assert "MAT_YELLOW_DANGER_SIGN" in rule_ids(r)


def test_maternal_third_trimester_bleeding_red_via_atp():
    r = run(maternal(trimester=3, maternal={"danger_signs": ["vaginal_bleeding"]}))
    assert r.urgency.value == "RED"
    assert "ATP_RED_THIRD_TRIMESTER_BLEEDING_DERIVED" in rule_ids(r)


def test_maternal_severe_hypertension_red_via_atp():
    r = run(maternal(vitals={"sbp": 170, "dbp": 112}))
    assert r.urgency.value == "RED"
    assert "ATP_RED_BP_HIGH" in rule_ids(r)


@pytest.mark.parametrize("hb,yellow", [(7.0, False), (6.9, True)])
def test_maternal_severe_anaemia(hb, yellow):
    r = run(maternal(maternal={"hb_g_dl": hb}))
    assert ("MAT_YELLOW_SEVERE_ANAEMIA" in rule_ids(r)) is yellow


def test_maternal_screen_not_done_is_not_green():
    r = run(maternal(maternal={"danger_sign_screen_completed": False}))
    assert r.urgency.value == "YELLOW"
    assert "maternal.danger_sign_screen_completed" in r.missing_fields


def test_maternal_age_advisory_does_not_change_urgency():
    r = run(maternal(age_years=17))
    assert r.urgency.value == "GREEN"
    assert [a.code for a in r.advisories] == ["MAT_AGE_RISK"]


# ── Chronic NCD ──


def ncd(readings, **kw):
    return case("chronic_ncd", chronic_ncd={"bp_readings": readings}, **kw)


def test_ncd_controlled_green():
    assert run(ncd([{"sbp": 132, "dbp": 84}, {"sbp": 128, "dbp": 82}])).urgency.value == "GREEN"


@pytest.mark.parametrize("reading,yellow", [({"sbp": 180, "dbp": 100}, False), ({"sbp": 181, "dbp": 100}, True), ({"sbp": 170, "dbp": 110}, False)])
def test_ncd_bp_over_180_110_yellow(reading, yellow):
    r = run(ncd([{"sbp": 130, "dbp": 80}, reading]))
    assert ("NCD_YELLOW_BP_OVER_180_110" in rule_ids(r)) is yellow


def test_ncd_reading_over_220_red_via_atp():
    r = run(ncd([{"sbp": 130, "dbp": 80}, {"sbp": 225, "dbp": 105}]))
    assert r.urgency.value == "RED"


def test_ncd_needs_two_readings_for_green():
    r = run(ncd([{"sbp": 130, "dbp": 80}]))
    assert r.urgency.value == "YELLOW"
    assert any(f.startswith("chronic_ncd.bp_readings") for f in r.missing_fields)


# ── Health camp ──


def test_health_camp_cbac_advisory_only():
    r = run(case("health_camp", health_camp={"cbac_score": 6}))
    assert r.urgency.value == "GREEN"
    assert [a.code for a in r.advisories] == ["CAMP_CBAC_HIGH"]
    assert run(case("health_camp", health_camp={"cbac_score": 4})).advisories == []


def test_health_camp_hypoxia_red():
    assert run(case("health_camp", vitals={"spo2": 86})).urgency.value == "RED"


# ── Campus fever ──


def test_campus_ili_advisory():
    r = run(case("campus_fever", vitals={"temp_c": 38.4}, campus_fever={"cough": True, "onset_days": 2}))
    assert [a.code for a in r.advisories] == ["CAMPUS_ILI"]
    assert r.urgency.value == "GREEN"  # NEWS2 1 (low); the ILI surveillance label never changes urgency
    assert "CAMPUS_ILI" not in rule_ids(r)


def test_campus_fever_over_39_red():
    r = run(case("campus_fever", vitals={"temp_c": 39.4}, campus_fever={"cough": True, "onset_days": 1}))
    assert r.urgency.value == "RED"
    assert "ATP_RED_FEVER_39" in rule_ids(r)


def test_campus_fever_sepsis_screen():
    r = run(case("campus_fever", suspected_infection=True, vitals={"temp_c": 38.6, "resp_rate": 22, "sbp": 98, "dbp": 65, "pulse": 96}))
    assert "QSOFA_POSITIVE" in rule_ids(r)
    assert r.urgency.value in ("YELLOW", "RED")


# ── Occupational ──


def test_occupational_sts_advisory_only():
    r = run(case("occupational", occupational={"sts_db_avg_2_3_4khz": 12}))
    assert r.urgency.value == "GREEN"
    assert [a.code for a in r.advisories] == ["OCC_STS"]


def test_occupational_trauma_red():
    assert run(case("occupational", red_flags_present=["dangerous_mechanism_trauma"])).urgency.value == "RED"


# ── Referral ──


def test_referral_incomplete_packet_advisory():
    r = run(case("referral", referral={"escort_arranged": True, "transport_arranged": False}))
    assert r.urgency.value == "GREEN"
    assert r.advisories[0].code == "REF_PACKET_INCOMPLETE"
    assert "transport_arranged" in r.advisories[0].message


def test_referral_complete_packet_no_advisory():
    full = {"escort_arranged": True, "transport_arranged": True, "identity_confirmed": True, "consent_taken": True}
    assert run(case("referral", referral=full)).advisories == []


def test_referral_red_patient_stays_red():
    assert run(case("referral", vitals={"resp_rate": 28})).urgency.value == "RED"
