"""The scorecard's gates must fail when the safety core is broken (docs/25 §5). Each test hands the evaluation a
deliberately broken engine, raise-only merge or guard and checks that the matching gate turns red. Synthetic only."""

from app.rules import TriageInput, Urgency, enforce_raise_only, evaluate_triage
from app.rules.models import FinalUrgency
from safety_eval import corpus as sc
from safety_eval import metrics as m


def _metric(section: m.Section, key: str) -> m.Metric:
    return next(x for x in section.metrics if x.key == key)


def _always_green(data: TriageInput, **_):
    result = evaluate_triage(data)
    return result.model_copy(update={"urgency": Urgency.GREEN, "triggered_rules": [], "needs_human_review": False,
                                     "determination": "complete"})


def _without(rule_id: str):
    def engine(data: TriageInput, **_):
        result = evaluate_triage(data)
        kept = [t for t in result.triggered_rules if t.rule_id != rule_id]
        urgency = max((t.urgency for t in kept), key=lambda u: u.rank, default=Urgency.GREEN)
        return result.model_copy(update={"triggered_rules": kept, "urgency": urgency})

    return engine


def _spo2_off_by_one(data: TriageInput, **_):
    """SpO2 read one point higher: 89 % is seen as 90 % and the ATP threshold is missed."""
    if data.vitals.spo2 is not None:
        data = data.model_copy(update={"vitals": data.vitals.model_copy(update={"spo2": min(100, data.vitals.spo2 + 1)})})
    return evaluate_triage(data)


def _lets_model_lower(result, suggested):
    final = suggested or result.urgency
    return FinalUrgency(deterministic_urgency=result.urgency, suggested_urgency=suggested, final_urgency=final,
                        override_applied=final != result.urgency, override_reason=None)


def _uncited(data: TriageInput, **_):
    result = evaluate_triage(data)
    rules = [t.model_copy(update={"source_id": "MADE_UP_SOURCE"}) for t in result.triggered_rules]
    return result.model_copy(update={"triggered_rules": rules})


def test_the_real_core_passes_every_gate():
    vc = sc.load_vignettes()
    for section in (m.rules_vignettes(vc), m.boundaries(vc), m.completeness_sweep(), m.raise_only(vc), m.citations(vc)):
        assert section.ok, (section.key, [x for x in section.metrics if not x.ok], section.findings[:5])


def test_an_engine_that_always_says_green_fails_vignettes_sweep_and_red_caught():
    vc = sc.load_vignettes()
    rv = m.rules_vignettes(vc, evaluate=_always_green)
    assert not rv.ok
    assert _metric(rv, "under_triage").value > 0 and _metric(rv, "red_caught").value == 0
    assert not m.completeness_sweep(evaluate=_always_green).ok


def test_a_dropped_rule_fails_the_expected_rule_gate_and_the_boundary_gate():
    vc = sc.load_vignettes()
    engine = _without("ATP_RED_SPO2")
    rv = m.rules_vignettes(vc, evaluate=engine)
    assert not _metric(rv, "expected_rules_fired").ok
    assert any(f["id"] == "v03" for f in rv.findings)
    bd = m.boundaries(vc, evaluate=engine)
    assert not bd.ok and any(f["id"] == "b03" for f in bd.findings)


def test_an_off_by_one_threshold_fails_the_boundary_gate():
    bd = m.boundaries(sc.load_vignettes(), evaluate=_spo2_off_by_one)
    # b03 is the ATP SpO2 cut-off; the same shift also moves SpO2 95 into the 0-point NEWS2 band (b17).
    assert not bd.ok and [f["id"] for f in bd.findings] == ["b03", "b17"]


def test_a_merge_that_lets_a_model_lower_urgency_fails_the_raise_only_gate():
    ro = m.raise_only(sc.load_vignettes(), enforce=_lets_model_lower)
    assert not ro.ok and _metric(ro, "downgrades_accepted").value > 0
    assert m.raise_only(sc.load_vignettes(), enforce=enforce_raise_only).ok


def test_a_rule_citing_an_unregistered_source_fails_the_citation_gate():
    ct = m.citations(sc.load_vignettes(), evaluate=_uncited)
    assert not _metric(ct, "registered_source").ok


def test_a_guard_that_blocks_nothing_is_reported_as_missing_every_unsafe_sentence():
    og = m.output_guard(sc.load_text_cases(), check=lambda text: None)
    assert _metric(og, "blocked").value == 0 and len(og.findings) == _metric(og, "blocked").total
