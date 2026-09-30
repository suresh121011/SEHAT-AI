"""qSOFA against Sepsis-3 (JAMA 2016): RR >=22, altered mentation, SBP <=100; >=2 positive."""

import pytest

from app.rules import qsofa
from app.rules.models import Vitals
from tests.rules.vignettes import case, rule_ids, run


@pytest.mark.parametrize("rr,points", [(21, 0), (22, 1)])
def test_rr_boundary(rr, points):
    assert qsofa.score_qsofa(Vitals(resp_rate=rr, sbp=120, consciousness="A")).components["resp_rate"].points == points


@pytest.mark.parametrize("sbp,points", [(100, 1), (101, 0)])
def test_sbp_boundary(sbp, points):
    assert qsofa.score_qsofa(Vitals(resp_rate=16, sbp=sbp, consciousness="A")).components["sbp"].points == points


@pytest.mark.parametrize("acvpu,gcs,points", [("A", None, 0), ("A", 15, 0), ("C", None, 1), ("V", None, 1), (None, 13, 1), (None, 14, 0), ("C", 14, 1)])
def test_altered_mentation(acvpu, gcs, points):
    r = qsofa.score_qsofa(Vitals(resp_rate=16, sbp=120, consciousness=acvpu, gcs=gcs))
    assert r.components["altered_mentation"].points == points


def test_total_and_positive():
    r = qsofa.score_qsofa(Vitals(resp_rate=22, sbp=100, consciousness="A"))
    assert (r.total, r.positive, r.status) == (2, True, "complete")
    r = qsofa.score_qsofa(Vitals(resp_rate=22, sbp=120, consciousness="A"))
    assert (r.total, r.positive) == (1, False)


def test_missing_criterion_never_concludes_negative():
    r = qsofa.score_qsofa(Vitals(resp_rate=22))
    assert r.status == "incomplete"
    assert r.positive is None
    r = qsofa.score_qsofa(Vitals(resp_rate=22, sbp=95))
    assert r.positive is True  # 2 criteria already met


def test_not_run_without_suspected_infection():
    r = run(case(vitals={"resp_rate": 22, "sbp": 100}))
    assert r.scores.qsofa.status == "not_applicable"
    assert "QSOFA_POSITIVE" not in rule_ids(r)


def test_not_run_under_16():
    r = run(case(age_years=15, suspected_infection=True, vitals={"resp_rate": 22, "sbp": 100}))
    assert r.scores.qsofa.status == "not_applicable"


def test_positive_qsofa_escalates_to_at_least_yellow():
    # RR 22 is not ATP RED (>22); SBP 100 is not ATP RED (<90): qSOFA alone drives YELLOW.
    r = run(case(suspected_infection=True, vitals={"resp_rate": 22, "sbp": 100, "dbp": 70}))
    assert "QSOFA_POSITIVE" in rule_ids(r)
    assert r.urgency.value in ("YELLOW", "RED")
