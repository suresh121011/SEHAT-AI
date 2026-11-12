# SEHAT AI — Agent Instructions

> **Single Source of Truth:** [`docs/sehat_ai_final_architecture__2.md`](docs/sehat_ai_final_architecture__2.md)

## Project Overview

SEHAT AI is a **rules-first, non-diagnostic, multimodal clinical triage and handoff engine** for Indian healthcare facilities. Built on Microsoft Semantic Kernel with Cognizant TriZetto AI Gateway alignment.

**Core Philosophy:** Deterministic AIIMS protocol rules set urgency. The LLM only extracts and summarises. A named human reviewer always signs off. Every field links to its source. Every referral is tracked to closure.

## Documentation Set

All project documentation lives in `docs/`:

| Document | Purpose |
|:---|:---|
| [`docs/01_PRD.md`](docs/01_PRD.md) | Product Requirements Document |
| [`docs/02_Features_Checklist.md`](docs/02_Features_Checklist.md) | Prioritised features with evaluation weight mapping |
| [`docs/03_Technical_Architecture.md`](docs/03_Technical_Architecture.md) | Condensed 6-layer pipeline and tech stack |
| [`docs/04_Security_Privacy_Access.md`](docs/04_Security_Privacy_Access.md) | Security, privacy, consent, DPDP-ready design |
| [`docs/05_Frontend_Specification.md`](docs/05_Frontend_Specification.md) | UI/UX screens, flows, components |
| [`docs/06_API_Data_Contracts.md`](docs/06_API_Data_Contracts.md) | API endpoints, JSON schemas, FHIR export |
| [`docs/07_Demo_Script.md`](docs/07_Demo_Script.md) | 5-minute Odisha-focused demo script |
| [`docs/08_Implementation_28h_Plan.md`](docs/08_Implementation_28h_Plan.md) | Phased 28-hour implementation plan |
| [`docs/09_Implementation_Todo_List.md`](docs/09_Implementation_Todo_List.md) | Granular checkbox tasks for each phase |
| [`docs/10_Safety_Rules_Engine.md`](docs/10_Safety_Rules_Engine.md) | Deterministic triage rules engine: verified clinical sources, decision record, test matrix |
| [`docs/11_Privacy_Consent_Audit.md`](docs/11_Privacy_Consent_Audit.md) | Consent, PII redaction, AI gateway, audit log — implemented controls, threat model, limitations (demo-only) |
| [`docs/12_Voice_Pipeline.md`](docs/12_Voice_Pipeline.md) | Voice pipeline (Phase 4): technology decisions, consent (`voice_cloud`), read-back policy, flags, verification status |
| [`docs/13_Language_Review_Checklist.md`](docs/13_Language_Review_Checklist.md) | Hindi/Odia draft wording awaiting native-speaker and clinical review; consent facts every language must state |
| [`docs/14_OCR_Pipeline.md`](docs/14_OCR_Pipeline.md) | OCR pipeline (Phase 5): Surya/PaddleOCR + Chandra on-device, Gödel verification, RxNorm, sourced reference ranges, source-linked review, verification status |
| [`docs/15_OCR_Evaluation_Plan.md`](docs/15_OCR_Evaluation_Plan.md) | Governed real-document OCR evaluation plan (not started; required before real patient data): governance, sampling, annotation, metrics, proposed acceptance criteria |
| [`docs/16_LLM_Extraction_MAKER.md`](docs/16_LLM_Extraction_MAKER.md) | LLM extraction (Phase 6): provider-agnostic adapter (fake, Azure by env, local open-weights model on a loopback llama-server), NeMo Guardrails layer (recommended on for demos), deterministic red-flag keyword suggester (labelled not AI output), grounding, MAKER voting, per-field review, source-linked note with raise-only urgency, missing info, counterfactuals, IndicTrans2 path, limits |
| [`docs/17_Reviewer_Dashboard.md`](docs/17_Reviewer_Dashboard.md) | Reviewer dashboard (Phase 8): rules-ordered queue, case review with source evidence, hypothetical counterfactuals, sign-off/override (reason codes), RED review target (display only, no alert delivery), governance aggregates, verification |
| [`docs/18_Medical_Image_Pipeline.md`](docs/18_Medical_Image_Pipeline.md) | Medical image visual-findings description (architecture §10A): classifier cross-check, fake/Gemini/Azure backends behind cloud + consent + synthetic-attestation gates, provenance on every item, local MedGemma 1.5 on this machine (`MEDGEMMA_BACKEND=local`: official weights hash-checked and converted locally, OCR worker, ECG never sent, smoke-tested only — not a validated medical device), field-level non-diagnostic guard, negation-aware keyword flags (not clinician-validated), confidence as a label, acknowledgement before sign-off, retention, verification (mocked only) |
| [`docs/19_Encounter_Summary.md`](docs/19_Encounter_Summary.md) | Clinical encounter summary: case-level, deterministic, source-linked summary from every recorded source (all extraction runs, voice read-back, documents incl. unchecked handwriting, triage vitals, reviewer status, body map); one trust vocabulary; grounding gate; staleness by source inventory; review without touching triage; root causes found; synthetic evaluation with denominators; limits |
| [`docs/20_Agent_Harness_and_Governance_Files.md`](docs/20_Agent_Harness_and_Governance_Files.md) | What every governance and agent file contains (CLAUDE.md, AGENTS.md, SKILLS.md, HARNESS.md, GUARDRAILS.md, ETHICS.md, MODEL_CARD.md, SECURITY.md, RUNBOOK.md, `.claude/`), who reads it, why it exists and when to update it |
| [`docs/21_Referral_Closure_Tracking.md`](docs/21_Referral_Closure_Tracking.md) | Referral closure tracking (Phase 2): signed-off referral packet, append-only forward-only status to closure, 48-hour overdue flag, printable letter, FHIR R4 export, supervisor metadata only, limits |
| [`docs/22_Lab_Timeline.md`](docs/22_Lab_Timeline.md) | Lab timeline (Phase 3): checked values across reports and linked visits, day-first report dates, higher/lower/same only when comparable, named gaps, no urgency, limits |
| [`docs/23_Protocol_Evidence.md`](docs/23_Protocol_Evidence.md) | Protocol evidence (Phase 4): the cited source and curated passage behind each fired rule, looked up by rule id; evidence gate (hash, source, licence, thresholds, named reviewer); locations only until passages are checked; closed-vocabulary search; no urgency, limits |

## Agent Rules

1. **Never invent features** not in the architecture document.
2. **Never claim diagnostic capability.** SEHAT AI is explicitly non-diagnostic (TPG 5.4, ICMR 2023).
3. **Never claim DPDP compliance.** Say "DPDP-ready by design." Substantive duties start May 2027.
4. **Never claim CDSCO clearance.** This is a "research prototype, not a clinically validated device."
5. **Rules-first always.** The LLM may only RAISE urgency, never lower it. Missing vitals = "needs human review", never "normal."
6. **Cognizant alignment.** Reference TriZetto AI Gateway and Microsoft Semantic Kernel where applicable.
7. **Source-linked evidence.** Every extracted field must trace to its source (audio timestamp, OCR bounding box, manual entry).
8. Verify clinical rules against current primary sources before implementation. The architecture document is a design spec, not a clinical protocol.

## Governance and agent files

| File | Purpose |
|:---|:---|
| [`CLAUDE.md`](CLAUDE.md) | Claude Code entry point: imports this file and adds the Claude-specific workflow |
| [`SKILLS.md`](SKILLS.md) | Index of the project skills in `.claude/skills/` and when each applies |
| [`HARNESS.md`](HARNESS.md) | How coding agents are constrained here: settings, hooks, skills, the review subagent, CI, and their limits |
| [`GUARDRAILS.md`](GUARDRAILS.md) | Every safety control: what it does, where it lives, which test pins it, how it fails, its limits; built vs planned |
| [`ETHICS.md`](ETHICS.md) | Principles, Indian and international frameworks, patient rights, consent, fairness, and what SEHAT does not claim |
| [`MODEL_CARD.md`](MODEL_CARD.md) | Every model and engine: pin, licence, intended use, measured results with denominators, failure modes |
| [`SECURITY.md`](SECURITY.md) | How to report a vulnerability; security controls and known limits |
| [`RUNBOOK.md`](RUNBOOK.md) | Setup, run, test, demo, troubleshooting, test baseline; `make help` lists the shortcuts |

## Repository map

| Path | What lives there |
|:---|:---|
| `backend/app/rules/` | Deterministic triage engine: AIIMS ATP, NEWS2, qSOFA, seven scenario packs, required fields, counterfactuals, cited sources. **The only urgency authority.** |
| `backend/app/ai/` | Extraction adapter and providers (`fake`, `local`, `azure`), MAKER voting, grounding, output guard, NeMo Guardrails layer, red-flag keyword suggester, per-field review, note, encounter summary |
| `backend/app/privacy/` | The only path from case text to a model: consent re-check, PII redaction, adapter contract |
| `backend/app/ocr/` | OCR engines and worker client, Gödel verification, RxNorm, sourced reference ranges, medical image descriptions |
| `backend/app/voice/`, `backend/app/interview/` | VAD, speech engines, read-back; the scripted voice interviewer |
| `backend/app/services/` | Semantic Kernel factory and the read-only visit tools for the assistant |
| `backend/app/routes/` | FastAPI routers; only those registered in `backend/app/main.py` are live |
| `backend/app/*.py` | Consent, audit, auth, sessions, OIDC, rate limits, security events, review and verification queues, case triage, patient registry and identity, config |
| `backend/tests/` | pytest suites by area; `tests/ci/` guards the CI skip gate and the agent harness |
| `backend/scripts/` | Model downloads, local model server, evaluations, end-to-end checks, CI skip gate, `doctor.py` |
| `frontend/src/` | Next.js 15 / React 19 app: `app/` routes, `components/`, `lib/` (with `node:test` unit tests) |
| `docs/` | Product and architecture docs (01–20), `plans/`, `implementation/`, `security/`, `integration/`, `clinical/` |

## Commands

`make help` lists the shortcuts (see `RUNBOOK.md`). The underlying commands:

| Task | Command |
|:---|:---|
| Backend tests (from `backend/`) | `SEHAT_DOTENV=0 ENVIRONMENT=test <venv>/bin/python -m pytest -q -p no:cacheprovider` |
| CI skip gate (from `backend/`) | full suite with `--junitxml=<file outside the repo>`, then `<venv>/bin/python scripts/ci_skip_gate.py <file>` |
| Frontend (from `frontend/`) | `npm ci`, then `npm test`, `npx tsc --noEmit`, `npx eslint .` |
| Environment check | `make doctor` (`backend/scripts/doctor.py`, read-only) |

The venv is `.venv` at the repo root of the main checkout; a worktree uses it by absolute path.

## CI gates (every pull request to `main`)

1. Backend: migration checks, the full suite, then `backend/scripts/ci_skip_gate.py` (no unexpected skips, every
   required safety module ran, at least `MIN_EXECUTED` tests ran).
2. Frontend: unit tests, `tsc --noEmit`, ESLint.
3. Secret scan (gitleaks).
4. Branch protection: the required checks pass and one approving review.

## Safety invariants

| Invariant | Enforced in | Pinned by |
|:---|:---|:---|
| The rules engine is the only urgency authority | `backend/app/case_triage.py`, `backend/app/rules/engine.py` | `tests/privacy/test_urgency_authority.py` |
| A model may raise urgency, never lower it | `enforce_raise_only` in `backend/app/rules/engine.py` | `tests/rules/test_override.py` |
| GREEN is earned; missing data means "needs human review" | `backend/app/rules/engine.py`, `required_fields.py` | `tests/rules/test_engine.py` |
| The rules engine has no LLM, network or storage imports; every rule cites a registered source | `backend/app/rules/` | `tests/rules/test_isolation.py` |
| Only the privacy gateway reaches a model, with consent re-checked | `backend/app/privacy/gateway.py` | `tests/privacy/test_boundary.py`, `tests/privacy/test_gateway.py` |
| Model values are schema-checked and grounded in a quote; MAKER disagreement goes to a human | `backend/app/ai/adapter.py`, `grounding.py`, `maker.py` | `tests/ai/test_pure.py` |
| Output text is non-diagnostic and non-prescriptive | `backend/app/ai/guard.py` (+ NeMo rails when enabled) | `tests/ai/test_pure.py`, `tests/ai/test_guardrails.py` |
| Case access and facility isolation; unknown and forbidden cases look the same | `backend/app/consent.py` | `tests/privacy/test_authz.py`, `tests/review/test_facility.py` |
| Append-only, hash-chained audit log | `backend/app/audit.py`, triggers in `backend/app/database.py` | `tests/privacy/test_audit.py` |
| Unsafe configuration refuses to start | `backend/app/config.py` | `tests/privacy/test_config_hardening.py` |

`GUARDRAILS.md` lists every control in detail, including the planned ones.

## Git and data rules

- Work in a worktree branched from `origin/main`. The main checkout holds the owner's work in progress.
- Stage files by name. Never `git add -A`, `git add .`, `-u` or `-f`; never `git commit -a`, `git reset`, `git clean`,
  `git stash` or force pushes.
- Commit only when the owner says "commit this". Pull requests go to `main` and need green CI and one approving review.
- Scratch databases only (`DATABASE_PATH=<scratch path>`); never `sehat.db`. Never read or print `.env`. No real
  patient data anywhere: code, tests, fixtures, logs, screenshots or docs.
- No global installs without the owner's explicit permission.
- Stop after each phase and wait for the owner's review.

For Claude Code, `.claude/settings.json` and the hooks in `.claude/hooks/` enforce most of these (`HARNESS.md`).

## Protected files (ask the owner before changing)

The urgency rules and sources (`backend/app/rules/`), the privacy gateway and redaction (`backend/app/privacy/`), the
output guard, NeMo rails, grounding, MAKER voting and red-flag keywords (`backend/app/ai/guard.py`, `guardrails.py`,
`grounding.py`, `maker.py`, `redflag_keywords.py`), the protocol evidence gate, passages and the owner's reviewer list
(`backend/app/evidence/gate.py`, `passages.jsonl`, `reviewers.json`), consent and the consent notice, audit, auth,
sessions, OIDC, rate limits, security events, patient identity, case triage, the review queue, the database schema and
triggers, config, the CI workflow and skip gate, the invariant tests named above, `.claude/settings.json`,
`.claude/hooks/`, `AGENTS.md` and `CLAUDE.md`. The authoritative list is `.claude/hooks/sehat_policy.py`.

## Work in progress: do not touch

The owner's main checkout holds unregistered, untracked work in progress: `backend/app/routes/referrals.py`,
`routes/cds.py`, `routes/patient.py`, `routes/surveillance.py`, `backend/app/cds/`, `backend/app/patient/`,
`backend/app/surveillance/`, their tests, and a modified `frontend/package.json`
(`docs/implementation/h0-1-wip-routers.md`).

- Do not import, register, edit, move or reuse these paths. New code uses new paths (for example
  `backend/app/referral_notes/`), so a `git pull` in the main checkout never collides with untracked files.
- Never re-enable `/cds/possibilities` in its current form: it returns ICD-10 codes and differentials, which is
  diagnostic (Agent Rule 2).

## Definition of done

1. Tests were written first; the full suites pass; counts are at or above the baseline in `RUNBOOK.md`.
2. New required test modules are in `REQUIRED_MODULES` of the skip gate, and `MIN_EXECUTED` is raised with a history line.
3. `GUARDRAILS.md`, `MODEL_CARD.md` and the affected docs are updated in the same change.
4. Safety changes have a `PASS` from the `sehat-safety-reviewer` subagent (or an equivalent human review).
5. UI changes were checked in the running app and the screenshots were looked at.
6. No claim goes beyond the evidence; every number has its denominator.
7. The pull request template's checklist is complete.
