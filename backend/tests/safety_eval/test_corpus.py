"""The safety-evaluation corpus itself (docs/25 §3): synthetic, labelled from cited criteria, complete enough to cover
every rule the engine can fire. Synthetic data only."""

import json

import pytest

from app.evidence.corpus import rule_catalogue
from app.rules.models import AtpFlag, Scenario
from app.rules.sources import SOURCES
from safety_eval import corpus as sc


@pytest.fixture(scope="module")
def vc():
    return sc.load_vignettes()


@pytest.fixture(scope="module")
def tc():
    return sc.load_text_cases()


def test_feature_31_asks_for_at_least_50_vignettes(vc):
    assert len(vc.vignettes) >= 50


def test_ids_are_unique_across_vignettes_and_probes(vc):
    ids = [v.id for v in vc.vignettes] + [b.id for b in vc.boundaries]
    assert len(ids) == len(set(ids))


def test_every_vignette_and_probe_builds_a_valid_engine_input(vc):
    for v in vc.vignettes:
        sc.build_input(v.scenario, v.input)
    for b in vc.boundaries:
        for overrides, _ in b.probes:
            sc.build_input(b.scenario, overrides)


def test_every_label_cites_a_registered_source_and_a_criterion(vc):
    for v in vc.vignettes:
        assert v.basis, v.id
        for source_id, criterion in v.basis:
            assert source_id in SOURCES, (v.id, source_id)
            assert criterion.strip(), v.id
    for b in vc.boundaries:
        assert b.basis[0] in SOURCES and b.basis[1].strip(), b.id


def test_expected_rule_ids_are_rules_the_engine_can_fire(vc):
    known = set(rule_catalogue())
    for v in vc.vignettes:
        assert set(v.expect.fired) | set(v.expect.not_fired) <= known, v.id
    for b in vc.boundaries:
        assert b.rule in known, b.id


def test_every_atp_flag_scenario_and_urgency_level_is_covered(vc):
    flags = {f for v in vc.vignettes for f in v.input.get("red_flags_present", [])}
    assert flags == {f.value for f in AtpFlag}
    assert {v.scenario for v in vc.vignettes} == {s.value for s in Scenario}
    assert {v.expect.urgency for v in vc.vignettes} == {"RED", "YELLOW", "GREEN"}


def test_every_rule_the_engine_can_fire_is_expected_by_some_labelled_vignette(vc):
    """A new rule needs a labelled vignette from its source before the scorecard passes."""
    expected = {r for v in vc.vignettes for r in v.expect.fired}
    assert set(rule_catalogue()) - expected == set()


def test_open_questions_are_valid_inputs_and_carry_no_label(vc):
    """Open questions record a gap for clinical review; giving them a label would decide a rule change by test."""
    assert vc.open_questions
    raw = json.loads((sc.DATA_DIR / "vignettes.json").read_text(encoding="utf-8"))["open_questions"]
    for q, r in zip(vc.open_questions, raw):
        sc.build_input(q.scenario, q.input)
        assert "expect" not in r and q.question.endswith("?")


def test_text_cases_have_unique_ids_and_every_category(tc):
    ids = ([c.id for c in tc.output_block] + [c.id for c in tc.output_pass] + [c.id for c in tc.input_flag]
           + [c.id for c in tc.input_pass] + [c.id for c in tc.pii_identifier] + [c.id for c in tc.pii_clinical])
    assert len(ids) == len(set(ids))
    assert {c.category for c in tc.output_block} == {"diagnostic", "prescriptive", "urgency_lowering", "review_bypass",
                                                     "instruction", "identifier"}
    assert len(tc.output_pass) >= 20 and len(tc.input_flag) >= 12 and len(tc.input_pass) >= 12


def test_corpus_files_say_they_are_synthetic_and_not_clinician_labelled():
    for name in ("vignettes.json", "text_cases.json"):
        about = json.loads((sc.DATA_DIR / name).read_text(encoding="utf-8"))["about"]
        assert "SYNTHETIC" in about and "not edited afterwards to make a result pass" in about
    assert "Not clinician-labelled" in json.loads((sc.DATA_DIR / "vignettes.json").read_text(encoding="utf-8"))["about"]


def test_scenario_bases_are_complete_normal_cases_that_earn_green():
    """The sweep removes one item at a time from these; each must start GREEN, complete and with no rule fired."""
    from app.rules import evaluate_triage

    for scenario in Scenario:
        result = evaluate_triage(sc.build_input(scenario.value, {}))
        assert (result.urgency.value, result.determination, result.triggered_rules) == ("GREEN", "complete", []), scenario
