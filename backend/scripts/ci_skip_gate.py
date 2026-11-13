"""CI skip gate (final remediation plan, CI-1). Fails the build when the test run is green for the wrong reason.

A pytest run passes even when a module-level ``importorskip`` silently drops a whole safety suite. This gate reads the
JUnit XML that pytest wrote and fails when:
  * any test was skipped for a reason that is not on the allowlist below;
  * a required safety module has a skipped test, or did not run at least one test;
  * fewer tests ran than ``MIN_EXECUTED``.

Changes to the allowlist, the required modules or the minimum are safety-relevant and need review like any test change.

Usage: python scripts/ci_skip_gate.py <junit.xml>
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# Optional components CI deliberately does not install. Matched against pytest's skip message.
ALLOWED_SKIP_REASONS: tuple[str, ...] = (
    r"could not import 'nemoguardrails'",  # CI-2: optional guardrails stack (requirements-guardrails.txt)
    r"\bset (SEHAT_LIVE_[A-Z_]+|RUN_LIVE_[A-Z_]+)=1\b",  # opt-in tests against real engines, models or paid APIs
    r"Known false negatives?\b.*Documents a weakness; NOT a pass",  # documented PII-scrubber misses (test_pii.py)
)

# Every one of these must run (no skips at all) or the build fails. JUnit classname prefixes.
REQUIRED_MODULES: tuple[str, ...] = (
    "tests.voice.test_api",
    "tests.voice.test_schema_consent",
    "tests.voice.test_consent_precheck",
    "tests.ai.test_encounter_summary",
    "tests.ai.test_encounter_summary_semantics",
    "tests.review.test_verification_queue",
    "tests.privacy.test_remediation_guards",
    "tests.privacy.test_migration_reconcile",
    "tests.privacy.test_migration_integrity",
    "tests.privacy.test_urgency_authority",
    "tests.services.test_kernel",
    "tests.review.test_supervisor_metadata_only",
    "tests.review.test_facility",
    "tests.privacy.test_config_hardening",
    "tests.ci.test_skip_gate",
    # post-remediation security hardening
    "tests.privacy.test_sessions",
    "tests.privacy.test_oidc",
    "tests.privacy.test_patient_identity",
    "tests.privacy.test_rate_limit",
    "tests.privacy.test_authz",
    "tests.interview.test_interview",
    # summary note from every source (docs/plans/summary-note-all-sources.md): images and interview answers in the
    # summary, the transparency rules, and the synthetic evaluation corpus (its safety metrics must all pass)
    "tests.ai.test_encounter_summary_sources",
    "tests.ai.test_encounter_summary_transparency",
    "tests.ai.test_encounter_eval",
    # per-symptom term and duration: grounding of a symptom's own duration, never a guessed number or a shared one
    "tests.ai.test_symptom_duration",
    # one Azure chat factory: allow-listed host, cloud and synthetic switches, truthful connection check
    "tests.services.test_kernel_factory",
    # the assistant's read-only visit tools: bound to one visit, no parameters, no writes, access and consent per call
    "tests.services.test_visit_tools",
    # the agent harness (HARNESS.md): the hooks that refuse broad git operations, secrets and the shared database,
    # and ask before safety-critical files change; the settings, skills and review-only subagent that wire them
    "tests.ci.test_agent_harness",
    # referral closure tracking (docs/21): access, consent, audit, forward-only append-only status, the overdue rule,
    # supervisor metadata only, the letter from the signed-off run, and the FHIR R4 bundle structure
    "tests.referral_notes.test_referral_pure",
    "tests.referral_notes.test_referral_api",
    "tests.referral_notes.test_referral_fhir",
    # lab timeline (docs/22): checked values only, day-first report dates, higher/lower only when comparable, gaps named,
    # linked visits only with link_visits consent, no urgency
    "tests.lab_timeline.test_timeline_pure",
    "tests.lab_timeline.test_timeline_api",
    # protocol evidence (docs/23): every fired rule has a corpus entry from its own source, the gate shows a passage
    # only when its hash, licence and review allow it, the index takes rule vocabulary only, the rules never import it
    "tests.evidence.test_evidence",
    # the fenced clinical assistant (docs/24): the answer gate, the closed question list, tools only from the question's
    # list, limits, the privacy gateway path, append-only runs, ids-only audit, who may call a chat model, the fence corpus
    "tests.assistant.test_assistant_gate",
    "tests.assistant.test_assistant",
    "tests.assistant.test_assistant_boundary",
    "tests.assistant.test_assistant_eval",
    # the safety scorecard (docs/25): the labelled corpus, the pinned results, and gates that fail on a broken engine,
    # threshold, raise-only merge or citation
    "tests.safety_eval.test_corpus",
    "tests.safety_eval.test_scorecard",
    "tests.safety_eval.test_mutations",
)

# History (raise it as tests are added; never lower it silently, a lower number needs the same review as removing a test):
#   1390 -> 1520 on 2026-11-10 (CI ran 1527 after the hardening phase)
#   1520 -> 1555 on 2026-11-11 (CI on main 0b5b23b ran 1541; this change adds 20 tests that run in CI: 14 summary-source,
#            1 corpus-shape and 5 gate tests, so 1561 are expected; the margin of 6 matches the earlier one)
#   1555 -> 1568 on 2026-11-12 (CI on main 4f7750a ran 1560; this change adds 14 per-symptom tests, so 1574 are expected)
#   1568 -> 1590 on 2026-11-12 (CI on main ed64e64 ran 1574; this change adds 24 tests, so 1598 are expected)
#   1590 -> 1604 on 2026-11-12 (CI on main 62d19fe ran 1598; this change adds 15 tests, so 1613 are expected)
#   1604 -> 1733 on 2026-11-12 (CI on main ba9bee5 ran 1613; this change adds 129 agent-harness tests, so 1742 are expected)
#   1733 -> 1774 on 2026-11-13 (CI on main 9132951 ran 1742; this change adds 41 referral tests, so 1783 are expected)
#   1774 -> 1800 on 2026-11-13 (main 10f8827 after the referral PR expects 1783; this change adds 26 lab-timeline tests, so 1809 are expected)
#   1800 -> 1828 on 2026-11-13 (main ab0096f after the lab-timeline PR expects 1809; this change adds 23 protocol-evidence
#            tests and 5 agent-harness cases for the newly protected evidence files, so 1837 are expected)
#   1828 -> 1962 on 2026-11-13 (main bbb6aa7 after the protocol-evidence PR expects 1837; this change adds 134 assistant tests,
#            so 1971 are expected)
#   1962 -> 2016 on 2026-11-13 (main 7f84e29 after the assistant PR expects 1971; this change adds 54 safety-scorecard
#            tests, so 2025 are expected)
MIN_EXECUTED = 2016


def _module(classname: str) -> str:
    # "tests.voice.test_api" or "tests.voice.test_api.TestX" -> the module part
    parts = classname.split(".")
    for i, p in enumerate(parts):
        if p.startswith("test_"):
            return ".".join(parts[: i + 1])
    return classname


def analyse(junit_xml: str) -> tuple[list[str], dict[str, int]]:
    """(problems, counts). Pure, so the gate itself is unit-tested. `counts` is what the CI log prints."""
    root = ET.fromstring(junit_xml)
    problems: list[str] = []
    executed: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    executed_total = allowlisted = unexpected = 0
    for case in root.iter("testcase"):
        mod = _module(case.get("classname") or case.get("name", ""))  # a collection skip has no classname
        skip = case.find("skipped")
        if skip is None:
            executed[mod] += 1
            executed_total += 1
            continue
        skipped[mod] += 1
        reason = (skip.get("message") or "") + " " + (skip.text or "")
        if any(re.search(p, reason) for p in ALLOWED_SKIP_REASONS):
            allowlisted += 1
        else:
            unexpected += 1
            problems.append(f"unexpected skip in {mod}::{case.get('name')}: {reason.strip()[:200]}")
    required_run = 0
    for mod in REQUIRED_MODULES:
        required_run += executed[mod]
        if skipped[mod]:
            problems.append(f"required safety module {mod} has {skipped[mod]} skipped test(s)")
        if not executed[mod]:
            problems.append(f"required safety module {mod} did not run")
    if executed_total < MIN_EXECUTED:
        problems.append(f"only {executed_total} tests ran; at least {MIN_EXECUTED} expected")
    counts = {"total": executed_total + sum(skipped.values()), "executed": executed_total, "skipped": sum(skipped.values()),
              "allowlisted_skips": allowlisted, "unexpected_skips": unexpected, "minimum_required": MIN_EXECUTED,
              "required_safety_modules": len(REQUIRED_MODULES), "required_safety_tests_executed": required_run,
              "required_safety_tests_skipped": sum(skipped[m] for m in REQUIRED_MODULES)}
    return problems, counts


def check(junit_xml: str) -> list[str]:
    """Return a list of problems (empty = pass)."""
    return analyse(junit_xml)[0]


def report(counts: dict[str, int]) -> str:
    return ("skip gate counts: total={total} executed={executed} skipped={skipped} allowlisted_skips={allowlisted_skips} "
            "unexpected_skips={unexpected_skips} minimum_required={minimum_required} | safety: modules={required_safety_modules} "
            "tests_executed={required_safety_tests_executed} tests_skipped={required_safety_tests_skipped}").format(**counts)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    problems, counts = analyse(Path(argv[1]).read_text())
    print(report(counts))
    for p in problems:
        print(f"SKIP GATE: {p}")
    if problems:
        return 1
    print("skip gate: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
