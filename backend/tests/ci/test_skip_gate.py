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
