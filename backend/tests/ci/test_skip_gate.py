"""The CI skip gate itself (final remediation plan, CI-1): a green run that silently skipped safety tests must fail."""

from scripts import ci_skip_gate as gate


def _xml(cases):
    body = "".join(cases)
    return f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


def _ok(mod, n=1):
    return [f'<testcase classname="{mod}" name="t{i}" />' for i in range(n)]


def _skip(mod, message):
    return f'<testcase classname="{mod}" name="s"><skipped type="pytest.skip" message="{message}">x</skipped></testcase>'


def _all_required(n=1):
    return [c for mod in gate.REQUIRED_MODULES for c in _ok(mod, n)]


def _filler(n):
    return _ok("tests.rules.test_filler", n)


def test_clean_run_passes():
    assert gate.check(_xml(_all_required() + _filler(gate.MIN_EXECUTED))) == []


def test_importorskip_of_a_required_module_fails():
    # The CI-1 failure mode: the module is collected as a single module-level skip and nothing in it runs.
    cases = [c for mod in gate.REQUIRED_MODULES if mod != "tests.voice.test_api" for c in _ok(mod)]
    cases.append(_skip("tests.voice.test_api", "could not import 'silero_vad': No module named 'silero_vad'"))
    problems = gate.check(_xml(cases + _filler(gate.MIN_EXECUTED)))
    assert any("unexpected skip in tests.voice.test_api" in p for p in problems)
    assert any("tests.voice.test_api did not run" in p for p in problems)


def test_allowlisted_skip_in_a_required_module_still_fails():
    cases = _all_required() + [_skip("tests.ai.test_encounter_summary", "could not import 'nemoguardrails'")]
    problems = gate.check(_xml(cases + _filler(gate.MIN_EXECUTED)))
    assert problems == ["required safety module tests.ai.test_encounter_summary has 1 skipped test(s)"]


def test_allowlisted_optional_skip_passes():
    cases = _all_required() + [_skip("tests.ai.test_guardrails", "could not import 'nemoguardrails': No module")]
    assert gate.check(_xml(cases + _filler(gate.MIN_EXECUTED))) == []


def test_unlisted_skip_anywhere_fails():
    cases = _all_required() + [_skip("tests.rules.test_x", "some new optional dependency")]
    problems = gate.check(_xml(cases + _filler(gate.MIN_EXECUTED)))
    assert len(problems) == 1 and problems[0].startswith("unexpected skip in tests.rules.test_x")


def test_too_few_tests_fails():
    problems = gate.check(_xml(_all_required()))
    assert len(problems) == 1 and "tests ran" in problems[0]


def test_class_based_test_ids_map_to_their_module():
    assert gate._module("tests.voice.test_api.TestConsent") == "tests.voice.test_api"
    assert gate._module("tests.voice.test_api") == "tests.voice.test_api"


# ── the counts the CI log prints, and the rules that stop the gate being bypassed (2026-11-11) ───────────────────


def test_counts_report_total_executed_skipped_allowlisted_unexpected_and_minimum():
    cases = _all_required(2) + _filler(gate.MIN_EXECUTED) + [
        _skip("tests.ai.test_guardrails", "could not import 'nemoguardrails': No module"),
        _skip("tests.rules.test_x", "some new optional dependency"),
    ]
    problems, counts = gate.analyse(_xml(cases))
    n_required = 2 * len(gate.REQUIRED_MODULES)
    assert counts == {"total": n_required + gate.MIN_EXECUTED + 2, "executed": n_required + gate.MIN_EXECUTED, "skipped": 2, "allowlisted_skips": 1,
                      "unexpected_skips": 1, "minimum_required": gate.MIN_EXECUTED, "required_safety_modules": len(gate.REQUIRED_MODULES),
                      "required_safety_tests_executed": n_required, "required_safety_tests_skipped": 0}
    assert len(problems) == 1
    line = gate.report(counts)
    for word in ("total=", "executed=", "skipped=", "allowlisted_skips=1", "unexpected_skips=1", f"minimum_required={gate.MIN_EXECUTED}"):
        assert word in line


def test_one_test_below_the_minimum_fails_and_exactly_the_minimum_passes():
    n = len(gate.REQUIRED_MODULES)
    assert gate.check(_xml(_all_required() + _filler(gate.MIN_EXECUTED - n))) == []
    problems = gate.check(_xml(_all_required() + _filler(gate.MIN_EXECUTED - n - 1)))
    assert problems == [f"only {gate.MIN_EXECUTED - 1} tests ran; at least {gate.MIN_EXECUTED} expected"]


def test_skipped_tests_never_count_towards_the_minimum():
    # Padding the run with allowlisted skips must not satisfy the minimum.
    skips = [_skip("tests.ai.test_guardrails", "could not import 'nemoguardrails'") for _ in range(gate.MIN_EXECUTED)]
    problems = gate.check(_xml(_all_required() + skips))
    assert len(problems) == 1 and "tests ran" in problems[0]


def test_the_minimum_and_the_required_list_are_never_lowered_silently():
    # A reduction is a reviewed change to this number, like removing a test. See the history beside MIN_EXECUTED.
    assert gate.MIN_EXECUTED >= 1604
    for mod in ("tests.ai.test_encounter_summary_sources", "tests.ai.test_encounter_eval", "tests.interview.test_interview",
                "tests.privacy.test_urgency_authority", "tests.ci.test_skip_gate"):
        assert mod in gate.REQUIRED_MODULES
    assert len(gate.REQUIRED_MODULES) == len(set(gate.REQUIRED_MODULES)) >= 27


def test_every_required_module_exists_on_disk():
    # A renamed or deleted safety file must fail here, not pass by never being looked for.
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for mod in gate.REQUIRED_MODULES:
        assert (root / (mod.replace(".", "/") + ".py")).is_file(), mod
