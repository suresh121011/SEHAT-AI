"""Permanent invariant (final remediation plan, step 1): the deterministic rules engine is the ONLY authority for urgency.

No AI, kernel, plugin, OCR, voice or summary path may compute a recorded urgency or write a triage run. These static
scans pin the whole app, not one module, so a new second urgency path fails CI even if no other test covers it.
A change to any allowlist below is a clinical-safety change and needs review.
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

# Modules allowed to call the rules engine, and why.
ENGINE_CALLERS = {
    "case_triage.py": "records a triage run from the engine result (the only writer)",
    "routes/triage.py": "stateless evaluation endpoint; writes nothing",
    "verification_queue.py": "hypothetical 'if confirmed' urgency for ordering checks; writes nothing",
    "services/plugins.py": "test harness wrapping the engine; not registered at runtime",
}
# Modules allowed to import the run writer: intake triage and a reviewer correction, both through the same rules.
RUN_WRITER_IMPORTERS = {"routes/cases.py", "review_queue.py"}
# Packages that must never reach the run writer or the review queue.
NO_TRIAGE_PACKAGES = ("ai", "voice", "ocr", "services", "privacy", "interview")

_WRITE_SQL = re.compile(r"\b(INSERT\s+(OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+triage_runs\b", re.IGNORECASE)


def _py_files():
    for p in sorted(APP.rglob("*.py")):
        rel = p.relative_to(APP).as_posix()
        yield rel, p.read_text(encoding="utf-8")


def _imports(src: str) -> set[tuple[str, str]]:
    """(module, name) pairs from `from X import Y`, plus (module, "") for `import X`."""
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            out |= {(node.module, a.name) for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {(a.name, "") for a in node.names}
    return out


def test_only_allowlisted_modules_call_the_rules_engine():
    callers = set()
    for rel, src in _py_files():
        if rel.startswith("rules/"):
            continue
        if any(mod.startswith("app.rules") and name == "evaluate_triage" for mod, name in _imports(src)):
            callers.add(rel)
    assert callers == set(ENGINE_CALLERS), f"unexpected rules-engine callers: {sorted(callers ^ set(ENGINE_CALLERS))}"


def test_only_case_triage_writes_triage_runs():
    writers = {rel for rel, src in _py_files() if _WRITE_SQL.search(src)}
    assert writers == {"case_triage.py"}, sorted(writers)


def test_the_recorded_urgency_is_the_engine_result():
    src = (APP / "case_triage.py").read_text(encoding="utf-8")
    assert "result = evaluate_triage(data)" in src
    insert_args = src[src.index("INSERT INTO triage_runs"):src.index("await audit.record")]
    assert "result.urgency.value" in insert_args, "triage_runs.urgency must come from the rules-engine result"


def test_only_intake_and_reviewer_correction_reach_the_run_writer():
    importers = set()
    for rel, src in _py_files():
        imps = _imports(src)
        if ("app", "case_triage") in imps or any(mod == "app.case_triage" for mod, _ in imps):
            importers.add(rel)
    assert importers == RUN_WRITER_IMPORTERS, sorted(importers)


def test_ai_voice_ocr_and_kernel_never_reach_triage_or_review_writers():
    offenders = []
    for rel, src in _py_files():
        if not rel.startswith(tuple(f"{p}/" for p in NO_TRIAGE_PACKAGES)):
            continue
        for mod, name in _imports(src):
            if mod in ("app.case_triage", "app.review_queue") or (mod == "app" and name in ("case_triage", "review_queue")):
                offenders.append(f"{rel}: {mod} {name}".strip())
    assert offenders == []
