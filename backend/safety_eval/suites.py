"""The slower part of the scorecard (`python -m safety_eval` without `--quick`): run the safety test modules, the
encounter-summary evaluation and the assistant-fence evaluation in one pytest process, exactly as CI runs them
(throwaway test databases, no `.env`, every provider off: the opt-in live switches are removed from the environment
first), and read the results from pytest's JUnit file."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from safety_eval.metrics import Metric, Section

BACKEND = Path(__file__).resolve().parents[1]

# A superset of `make safety-test` (pinned by tests/safety_eval/test_scorecard.py), plus PII redaction, note
# regressions, protocol evidence, the assistant and the two synthetic evaluations.
SAFETY_MODULES: tuple[str, ...] = (
    "tests/rules",
    "tests/privacy/test_urgency_authority.py",
    "tests/privacy/test_boundary.py",
    "tests/privacy/test_gateway.py",
    "tests/privacy/test_consent.py",
    "tests/privacy/test_authz.py",
    "tests/privacy/test_audit.py",
    "tests/privacy/test_config_hardening.py",
    "tests/privacy/test_pii.py",
    "tests/ai/test_pure.py",
    "tests/ai/test_note_regressions.py",
    "tests/ai/test_redflag_keywords.py",
    "tests/ai/test_encounter_eval.py",
    "tests/ocr/test_safety_boundary.py",
    "tests/review/test_red_lifecycle.py",
    "tests/review/test_facility.py",
    "tests/evidence",
    "tests/assistant",
    "tests/ci",
)
FENCE_MODULE = "tests.assistant.test_assistant_eval"
SUMMARY_MODULE = "tests.ai.test_encounter_eval"
_ROW = re.compile(r"^\| ([^|]+?) \| (\d+) / (\d+) \|", re.M)


LIVE_PREFIXES = ("SEHAT_LIVE_", "RUN_LIVE_")


def pytest_env(summary: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """The shell's environment minus the opt-in live switches. With a live switch set, tests/conftest.py would keep the
    shell's provider keys and database path; without one it removes them, so every provider stays off."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if not k.startswith(LIVE_PREFIXES)}
    return {**env, "SEHAT_DOTENV": "0", "ENVIRONMENT": "test", "ENCOUNTER_EVAL_REPORT": str(summary)}


def run_pytest(out_dir: Path) -> tuple[int, Path, Path]:
    junit, summary = out_dir / "safety-suites.junit.xml", out_dir / "encounter-summary-eval.md"
    env = pytest_env(summary)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", *SAFETY_MODULES]
    code = subprocess.call(cmd, cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return code, junit, summary


def _cases(junit: Path) -> list[tuple[str, str]]:
    """(classname, outcome) with outcome passed / failed / xfail / skipped."""
    out = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        outcome = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed"
                break
            if child.tag == "skipped":
                outcome = "xfail" if child.get("type") == "pytest.xfail" else "skipped"
        out.append((case.get("classname", ""), outcome))
    return out


def suite_section(junit: Path, exit_code: int) -> Section:
    cases = _cases(junit)
    counts = Counter(outcome for _, outcome in cases)
    countable = len(cases) - counts["xfail"]
    modules = Counter(name.rsplit(".", 1)[0] if name.count(".") > 2 else name for name, _ in cases)
    failing = sorted({name for name, outcome in cases if outcome in ("failed", "skipped")})
    findings = [{"id": name, "problem": "failed or skipped"} for name in failing]
    finished = exit_code in (0, 1)  # 1 = some tests failed (counted below); 2-5 = interrupted, usage or collection error
    if not finished:
        findings.append({"id": "pytest", "problem": f"pytest exited with code {exit_code} (collection or usage error)"})
    return Section(
        key="safety_suites",
        title="Safety test suites",
        what=(f"{len(SAFETY_MODULES)} test paths ({len(modules)} modules): rules, urgency authority, model boundary, gateway, "
              "consent, access, audit, config hardening, PII, guards, keywords, OCR boundary, RED lifecycle, facility "
              "isolation, protocol evidence, the assistant, CI gates."),
        metrics=[
            Metric("pytest_finished", "pytest ran to the end (no collection, usage or interruption error)", int(finished), 1, "all"),
            Metric("tests_passed", "Tests passed (expected failures excluded)", counts["passed"], countable, "all"),
            Metric("unexpected_skips", "Tests skipped (none allowed in the safety suites)", counts["skipped"], len(cases), "none"),
            Metric("known_gaps", "Documented known gaps (expected failures, e.g. missed lower-case names)", counts["xfail"], len(cases)),
        ],
        findings=findings,
        limits=["Tests pin behaviour the authors thought of; they are not proof of safety."],
    )


def fence_counts() -> tuple[int, int, int]:
    """(scripted answers, sentences, sentences that must be dropped) in the assistant evaluation corpus."""
    from tests.assistant.test_assistant_eval import CASES

    sentences = [s for case in CASES for s in case[2]]
    return len(CASES), len(sentences), sum(1 for s in sentences if s[2] != "keep")


def fence_section(junit: Path) -> Section:
    cases = [o for name, o in _cases(junit) if name == FENCE_MODULE]
    passed = bool(cases) and all(o == "passed" for o in cases)
    answers, sentences, to_drop = fence_counts()
    return Section(
        key="assistant_fence",
        title="Clinical assistant fence (scripted model replies)",
        what=(f"The assistant's answer gate, tool list and limits on {answers} scripted answers (docs/24 §7): cited facts "
              "only, no new numbers, no urgency statements, no diagnosis or prescription, no unknown tools. One test checks "
              "every sentence, so the result is all or nothing."),
        metrics=[
            Metric("fence_sentences", "Scripted sentences kept or dropped exactly as expected", sentences if passed else 0,
                   max(sentences, 1), "all"),
            Metric("fence_unsafe_sentences", "  of which unsafe sentences that had to be dropped", to_drop if passed else 0,
                   max(to_drop, 1), "all"),
        ],
        limits=["Mock mode: it measures the fence, not the model. The real-Azure run is not done yet."],
    )


def summary_section(junit: Path, report: Path) -> Section:
    cases = [o for name, o in _cases(junit) if name == SUMMARY_MODULE]
    metrics = [Metric("summary_eval_tests", "Encounter-summary evaluation tests passed (its safety metrics must all be 100 %)",
                      sum(o == "passed" for o in cases), max(len(cases), 1), "all")]
    if report.is_file():
        text = report.read_text(encoding="utf-8")
        metrics += [Metric(f"summary_{i}", f"  {label.strip()}", int(ok), int(n)) for i, (label, ok, n) in enumerate(_ROW.findall(text))
                    if int(n) > 0]
    return Section(
        key="encounter_summary",
        title="Encounter summary on synthetic cases",
        what="The deterministic, source-linked summary note on the synthetic corpus (docs/19 §8), with a keyword extractor "
             "standing in for the model and replayed OCR.",
        metrics=metrics,
        limits=["Measures the summary stage on hand-written synthetic cases, not handwriting or speech recognition."],
    )


def run_full(out_dir: Path) -> list[Section]:
    code, junit, summary = run_pytest(out_dir)
    if not junit.is_file():
        return [Section(key="safety_suites", title="Safety test suites", what="pytest did not write a JUnit file",
                        metrics=[Metric("tests_passed", "Tests passed", 0, 1, "all")],
                        findings=[{"id": "pytest", "problem": f"exit code {code}, no JUnit file"}])]
    return [suite_section(junit, code), summary_section(junit, summary), fence_section(junit)]
