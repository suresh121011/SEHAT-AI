---
name: sehat-safety-change
description: Use before and during any SEHAT AI change that touches urgency rules, the rules engine, clinical thresholds or sources, consent, PII redaction, the AI gateway, model prompts or providers, output guards, grounding, red-flag keywords, audit, auth, sessions, facility isolation, migrations of append-only tables, CI gates, or clinical or patient-facing wording. A checklist that keeps the change rules-first, non-diagnostic, consented, audited and tested.
---

# SEHAT AI safety change

SEHAT AI is a research prototype, not a clinically validated device. A safety change is any change that could alter
urgency, what a patient is told, what leaves the machine, who can see a case, or what CI proves. Follow every step.

## 1. Before you write code

- Read `AGENTS.md` (Agent Rules 1–8 and the safety invariants) and the matching section of `GUARDRAILS.md`.
- Work in a worktree from `origin/main`, never in the main checkout (it holds the owner's work in progress).
- **Urgency rules need the owner's approval first.** A new or changed threshold, rule, scenario pack or entry in
  `backend/app/rules/sources.py` needs: a current primary source (Agent Rule 8), the exact table/page, and a yes from
  the owner. Do not proceed on a rule change without all three.
- Never route a clinical decision through a model. Models extract and summarise; the rules engine decides urgency;
  a named human signs off.

## 2. Tests first

Write the failing test before the change. Pin the behaviour that must not regress:

| Change touches | Add or extend tests in |
|---|---|
| Rules, thresholds, scenarios | `backend/tests/rules/` (cite the source in the test), `test_isolation.py` must stay green |
| Anything that could set urgency | `backend/tests/privacy/test_urgency_authority.py` must pass **unchanged** |
| Model calls, prompts, providers | `backend/tests/privacy/test_boundary.py`, `tests/privacy/test_gateway.py`, `tests/ai/` |
| Output text shown to staff or patients | `backend/app/ai/guard.py` tests (`tests/ai/test_pure.py`, `test_note_regressions.py`) |
| Consent, access, facility scope | `tests/privacy/test_consent.py`, `test_authz.py`, `tests/review/test_facility.py` |
| Audit, append-only tables, migrations | `tests/privacy/test_audit.py`, `test_migration*.py` |
| Sessions, OIDC, rate limits | `tests/privacy/test_sessions.py`, `test_oidc.py`, `test_rate_limit.py` |

Use synthetic data only. Never a real name, phone, Aadhaar or ABHA number, even in a fixture.

## 3. While you change code

- Missing data → "needs human review", never "normal" and never GREEN.
- A model suggestion may raise urgency only through `enforce_raise_only` (`backend/app/rules/engine.py`).
- Every value shown to a clinician links to its source (quote, audio timestamp, OCR box or manual entry).
- New routes: case access and facility scope (`app/consent.py`), the right consent purpose, an audit event in the same
  transaction, append-only events instead of in-place updates.
- New text: no diagnosis, differential, prescription, dose or reassurance ("normal", "nothing to worry about").
- Never reuse the WIP paths (`routes/referrals.py`, `app/cds/`, `app/patient/`, `app/surveillance/`): new code goes
  in new modules (for example `app/referral_notes/`).

## 4. Before you say it is done

1. Run the focused tests, then the full suites (skill `sehat-run-tests`). Report exact counts against the baseline.
2. If you added test modules that must always run, add them to `REQUIRED_MODULES` in
   `backend/scripts/ci_skip_gate.py` and raise `MIN_EXECUTED` with a history line. Never lower it.
3. Ask the `sehat-safety-reviewer` subagent to review the diff. Fix every BLOCK finding.
4. Update `GUARDRAILS.md` (and `MODEL_CARD.md` for model changes) in the same change.
5. If the change is visible in the app, run the real-app check (skill `sehat-real-app-check`) and look at the screenshots.
6. Stage files by name. Commit only when the owner says "commit this".
