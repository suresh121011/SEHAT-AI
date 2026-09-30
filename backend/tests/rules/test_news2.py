"""NEWS2 scoring against RCP 2017 Chart 1 bands, including every boundary."""

import pytest

from app.rules import news2
from app.rules.models import Vitals
from tests.rules.vignettes import NORMAL_VITALS


@pytest.mark.parametrize("rr,points", [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3), (0, 3)])
def test_resp_rate_bands(rr, points):
    assert news2.resp_rate_points(rr) == points


@pytest.mark.parametrize("spo2,points", [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (100, 0)])
def test_spo2_scale1_bands(spo2, points):
    assert news2.spo2_scale1_points(spo2) == points


@pytest.mark.parametrize(
    "spo2,on_o2,points",
    [(83, False, 3), (84, False, 2), (85, True, 2), (86, False, 1), (87, True, 1), (88, True, 0), (92, True, 0),
     (93, False, 0), (99, False, 0), (93, True, 1), (94, True, 1), (95, True, 2), (96, True, 2), (97, True, 3)],
)
def test_spo2_scale2_bands(spo2, on_o2, points):
    assert news2.spo2_scale2_points(spo2, on_o2) == points


@pytest.mark.parametrize("sbp,points", [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3)])
def test_sbp_bands(sbp, points):
    assert news2.sbp_points(sbp) == points


@pytest.mark.parametrize("pulse,points", [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2), (130, 2), (131, 3)])
def test_pulse_bands(pulse, points):
    assert news2.pulse_points(pulse) == points


@pytest.mark.parametrize("temp,points", [(35.0, 3), (35.1, 1), (36.0, 1), (36.1, 0), (38.0, 0), (38.1, 1), (39.0, 1), (39.1, 2)])
def test_temperature_bands(temp, points):
    assert news2.temp_points(temp) == points


@pytest.mark.parametrize("acvpu,points", [("A", 0), ("C", 3), ("V", 3), ("P", 3), ("U", 3)])
def test_consciousness(acvpu, points):
    assert news2.consciousness_points(acvpu) == points


def test_oxygen_scores_two():
    assert news2.oxygen_points(True) == 2
    assert news2.oxygen_points(False) == 0


def test_normal_total_zero_low_band():
    r = news2.score_news2(Vitals(**NORMAL_VITALS))
    assert (r.status, r.total, r.band) == ("complete", 0, "low")


def test_hand_computed_total_seven_is_high():
    # RR 22 (2) + SpO2 94 (1) + air (0) + SBP 105 (1) + pulse 112 (2) + A (0) + 38.5 (1) = 7
    v = Vitals(resp_rate=22, spo2=94, on_supplemental_oxygen=False, sbp=105, pulse=112, consciousness="A", temp_c=38.5)
    r = news2.score_news2(v)
    assert (r.total, r.band) == (7, "high")


def test_total_five_is_medium_and_single_three_is_low_medium():
    v5 = Vitals(resp_rate=22, spo2=95, on_supplemental_oxygen=False, sbp=105, pulse=95, consciousness="A", temp_c=37.0)
    assert (news2.score_news2(v5).total, news2.score_news2(v5).band) == (5, "medium")
    v3 = Vitals(**{**NORMAL_VITALS, "consciousness": "C"})
    assert (news2.score_news2(v3).total, news2.score_news2(v3).band) == (3, "low_medium")


def test_missing_parameter_is_incomplete_not_normal():
    r = news2.score_news2(Vitals(**{**NORMAL_VITALS, "temp_c": None}))
    assert r.status == "incomplete"
    assert r.total is None
    assert r.partial_total == 0
    assert "temp_c" in r.reason


def test_incomplete_partial_total_still_reports_band_lower_bound():
    r = news2.score_news2(Vitals(resp_rate=26, spo2=90, pulse=135))  # 3 + 3 + 3 = 9 from 3 parameters
    assert r.status == "incomplete"
    assert (r.partial_total, r.band) == (9, "high")


def test_scale2_at_or_above_93_needs_oxygen_status():
    r = news2.score_news2(Vitals(**{**NORMAL_VITALS, "spo2_scale": 2, "spo2": 95, "on_supplemental_oxygen": None}))
    assert r.components["spo2"].points is None
    assert r.status == "incomplete"
