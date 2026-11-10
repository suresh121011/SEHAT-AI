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
    "tests.ai.test_encounter_summary",
    "tests.review.test_verification_queue",
    "tests.privacy.test_remediation_guards",
    "tests.privacy.test_migration_reconcile",
    "tests.privacy.test_urgency_authority",
    "tests.services.test_kernel",
    "tests.ci.test_skip_gate",
)

MIN_EXECUTED = 1390  # CI ran 1393 on 2026-11-10 (guardrails not installed); raise it as tests are added, never lower it silently


def _module(classname: str) -> str:
    # "tests.voice.test_api" or "tests.voice.test_api.TestX" -> the module part
    parts = classname.split(".")
    for i, p in enumerate(parts):
        if p.startswith("test_"):
            return ".".join(parts[: i + 1])
    return classname


def check(junit_xml: str) -> list[str]:
    """Return a list of problems (empty = pass). Pure function so the gate itself is unit-tested."""
    root = ET.fromstring(junit_xml)
    problems: list[str] = []
    executed: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    executed_total = 0
    for case in root.iter("testcase"):
        mod = _module(case.get("classname") or case.get("name", ""))  # a collection skip has no classname
        skip = case.find("skipped")
        if skip is None:
            executed[mod] += 1
            executed_total += 1
            continue
        skipped[mod] += 1
        reason = (skip.get("message") or "") + " " + (skip.text or "")
        if not any(re.search(p, reason) for p in ALLOWED_SKIP_REASONS):
            problems.append(f"unexpected skip in {mod}::{case.get('name')}: {reason.strip()[:200]}")
    for mod in REQUIRED_MODULES:
        if skipped[mod]:
            problems.append(f"required safety module {mod} has {skipped[mod]} skipped test(s)")
        if not executed[mod]:
            problems.append(f"required safety module {mod} did not run")
    if executed_total < MIN_EXECUTED:
        problems.append(f"only {executed_total} tests ran; at least {MIN_EXECUTED} expected")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    problems = check(Path(argv[1]).read_text())
    for p in problems:
        print(f"SKIP GATE: {p}")
    if problems:
        return 1
    print("skip gate: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
