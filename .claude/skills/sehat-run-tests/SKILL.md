---
name: sehat-run-tests
description: Use to run SEHAT AI's backend and frontend test suites the way CI does (including the migration checks and the skip gate), to compare results with the recorded baseline, or to find which focused test module covers an area. Covers worktrees, where the project venv lives in the main checkout.
---

# SEHAT AI tests

CI (`.github/workflows/ci.yml`) runs three jobs on every pull request: backend tests + skip gate, frontend tests +
TypeScript + ESLint, and a gitleaks secret scan. Run the same locally before you say a change is done.

## Which Python

The project venv is `.venv` at the repo root (`sehat-ai/.venv`). A worktree has no venv of its own: use the main
checkout's interpreter by absolute path, for example `PY=/path/to/sehat-ai/.venv/bin/python`. Never install into the
system Python.

## Commands

`make` targets do all of this (`make help`). By hand, from `backend/`:

```bash
# the whole backend suite, isolated from any developer .env
SEHAT_DOTENV=0 ENVIRONMENT=test "$PY" -m pytest -q -p no:cacheprovider

# CI parity: migration checks, full suite with JUnit, then the skip gate
SEHAT_DOTENV=0 ENVIRONMENT=test "$PY" -m pytest -q -p no:cacheprovider tests/privacy/test_migration*.py tests/voice/test_schema_consent.py
SEHAT_DOTENV=0 ENVIRONMENT=test "$PY" -m pytest -q -p no:cacheprovider -rs --junitxml="${TMPDIR:-/tmp}/sehat-junit.xml"
"$PY" scripts/ci_skip_gate.py "${TMPDIR:-/tmp}/sehat-junit.xml"
```

From `frontend/` (after `npm ci`):

```bash
npm test
npx tsc --noEmit
npx eslint .
```

Rules: always `SEHAT_DOTENV=0 ENVIRONMENT=test` for tests (a developer `.env` must not change results); always
`-p no:cacheprovider` (no `.pytest_cache` in the tree); write JUnit files outside the repo.

## Reading the result

- **Skips.** Locally the 14 skips are opt-in live tests (`SEHAT_LIVE_*` / `RUN_LIVE_*`). CI also skips the NeMo
  Guardrails tests (the optional package is not installed there). The skip gate fails on any other skip and on a
  required safety module that did not run.
- **xfail.** The 5 expected failures document known PII-scrubber misses (`tests/privacy/test_pii.py`). They are
  weaknesses on record, not passes.
- **Baseline.** Compare with the latest numbers in `RUNBOOK.md` ("Test baseline"). Report exact counts: passed,
  skipped, xfailed, failed. A drop in passed tests with no removed tests is a failure even if nothing is red.

## Focused runs

| Area | Command (from `backend/`) |
|---|---|
| Rules engine | `"$PY" -m pytest -q -p no:cacheprovider tests/rules` |
| Urgency authority and boundaries | `... tests/privacy/test_urgency_authority.py tests/privacy/test_boundary.py tests/rules/test_isolation.py` |
| AI extraction, guard, grounding | `... tests/ai` |
| Consent, privacy, auth | `... tests/privacy` |
| Reviewer workflow | `... tests/review` |
| Agent harness hooks | `... tests/ci/test_agent_harness.py` |

Prefix each with `SEHAT_DOTENV=0 ENVIRONMENT=test`.
