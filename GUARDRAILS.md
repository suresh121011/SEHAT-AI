# SEHAT AI: guardrails

SEHAT AI is a **research prototype, not a clinically validated device**. It is rules-first and non-diagnostic: a
deterministic engine sets urgency from published protocols, models only extract and summarise, and a named clinician
signs off. This file lists every safety control: what it does, where it lives, which test pins it, how it fails, and
its limits. Paths are relative to the repo root; tests are under `backend/tests/`.

**Status:** Built = in code and covered by tests. Partial = built with a stated gap. Planned = not built yet.
Nothing marked Planned may be presented as working.

## 1. Clinical decision safety

| Control | What it does | Code | Pinned by | On failure | Limits | Status |
|:---|:---|:---|:---|:---|:---|:---|
| Deterministic triage engine | AIIMS Triage Protocol, NEWS2, qSOFA and seven scenario packs; urgency is the maximum of all triggered rules (RED > YELLOW > GREEN). Pure: no I/O, no network, no model | `backend/app/rules/engine.py`, `atp.py`, `news2.py`, `qsofa.py`, `scenarios/` | `tests/rules/` | Invalid input is rejected by validation | Thresholds come from cited sources (`docs/10`); not clinically validated in deployment | Built |
| GREEN must be earned | GREEN only with complete required data and a completed red-flag screen; anything missing means "needs human review", never "normal" | `backend/app/rules/engine.py`, `required_fields.py` | `tests/rules/test_engine.py` (`test_missing_required_data_is_never_green`, child and pregnancy cases) | Falls to YELLOW with human review | Required-field lists follow the architecture document | Built |
| One urgency authority | Only case triage runs the engine and writes `triage_runs`; no AI, kernel, OCR, voice or summary path can | `backend/app/case_triage.py` | `tests/privacy/test_urgency_authority.py` (static scans of the whole app) | CI fails on a second urgency path | Static analysis of this repo only | Built |
| Raise-only model suggestions | A model's urgency suggestion can raise the result, never lower it; a refused downgrade is recorded with its reason | `enforce_raise_only` in `backend/app/rules/engine.py` | `tests/rules/test_override.py` | Downgrade refused | — | Built |
| Isolated, cited rules | The rules package imports no model, network or storage code; every rule names a registered source | `backend/app/rules/registry.py`, `sources.py` | `tests/rules/test_isolation.py` | CI fails | Sources must be re-verified when protocols change (Agent Rule 8) | Built |
| Counterfactual explanations | "If X were Y, urgency would be Z", found by re-running the engine, never by new thresholds | `backend/app/rules/counterfactual.py` | `tests/ai/test_p1.py` | — | Hypothetical; shown as such | Built |
| Red-flag screen is always asked | Completion is never inferred from free text; a human records it | `backend/app/ai/followup.py`, engine | `tests/rules/test_engine.py` | Missing screen blocks GREEN | — | Built |
| Reference ranges never set urgency | Lab values read by OCR are flagged against cited intervals for a clinician; the engine never imports this module | `backend/app/rules/reference_ranges.py` | `tests/ocr/test_safety_boundary.py` (reviewed OCR values never change urgency), `tests/ocr/test_reference_ranges.py` | — | Intervals are population references, not diagnosis | Built |
| Named sign-off and override reasons | Review actions are append-only events bound to one triage run; an override needs a reason code ("other" needs at least 10 characters) | `backend/app/review_queue.py` | `tests/review/test_review_api.py`, `test_corrections.py` | Request rejected | Shared demo accounts cannot prove who signed (demo only) | Built |
| RED review target | 180 s deadline computed server-side; an overdue RED is recorded once in the audit log; a lower re-run keeps RED priority | `backend/app/review_queue.py` | `tests/review/test_red_lifecycle.py` | — | **Display only: no SMS, pager or alert delivery** | Partial |
| Referral closure tracking | A medical officer refers only from a signed-off triage; packet urgency is the rules urgency raised (never lowered) by an override; closed-list fields; append-only, forward-only status; overdue after 48 h without an update before arrival; letter and FHIR R4 export built from the signed-off run; supervisors see metadata only (docs/21) | `backend/app/referral_notes/`, `routes/referral_notes.py` | `tests/referral_notes/`, `tests/review/test_facility.py` | Wrong role 403; stale run, missing sign-off, open referral, stale or invalid status 409; consent refusal audited | **Overdue is displayed, not delivered** (no SMS); demo facility list; FHIR structure-checked, not validated | Built |

## 2. AI and model controls

| Control | What it does | Code | Pinned by | On failure | Limits | Status |
|:---|:---|:---|:---|:---|:---|:---|
| Single gateway to a model | Case access → consent check → redaction → consent re-check → adapter. Nothing else may call a model adapter | `backend/app/privacy/gateway.py` | `tests/privacy/test_boundary.py`, `tests/privacy/test_gateway.py` | Consent changed: 409, adapter not called | An application safeguard, not a sandbox | Built |
| PII redaction before any model | Presidio analyzer, spaCy `en_core_web_sm` names and places, India patterns (Aadhaar-like, ABHA number and address, Indian mobile, PAN, dates of birth) | `backend/app/privacy/pii.py` | `tests/privacy/test_pii.py` | — | **Heuristic, not anonymization**; weak on Indian names; 5 known misses documented as expected failures | Partial |
| Structured output only | Providers return JSON; the server validates the schema; provider output carries no authority | `backend/app/ai/adapter.py` | `tests/ai/test_pure.py` | Invalid reply abstains; all passes invalid: 502 | — | Built |
| Grounding | Every value must cite a segment and a quote that occurs in it; numbers must appear in the quote; negation handled asymmetrically | `backend/app/ai/grounding.py` | `tests/ai/test_pure.py`, `test_symptom_duration.py` | Ungrounded value is not accepted | Quote match is lexical, not semantic | Built |
| MAKER voting | Same extraction run 3–5 times (temperatures 0.1/0.2/0.3); unanimous = agreed, majority = majority, disagreement goes to a human | `backend/app/ai/maker.py` | `tests/ai/test_pure.py` | Disputed field shown for review | Passes share the model's blind spots | Built |
| Red-flag keyword suggester | Deterministic phrases suggest red-flag candidates, labelled "Keyword rule, not AI model output", never ticked automatically | `backend/app/ai/redflag_keywords.py` | `tests/ai/test_redflag_keywords.py` | — | Author-written list, not clinician-validated | Built |
| Non-diagnostic output guard | Blocks sentences that read as a diagnosis, a prescription, an instruction to a model, or that still contain an identifier | `backend/app/ai/guard.py` | `tests/ai/test_pure.py`, `tests/test_medgemma.py` | Sentence blocked | **Regex: can be evaded**; a backstop behind sign-off | Built |
| NeMo Guardrails layer (optional; on for demos) | Input rail on redacted text before any provider call; output rail on voted values; telemetry forced off; no model inside the rails | `backend/app/ai/guardrails.py` | `tests/ai/test_guardrails.py` (skipped in CI, where the package is not installed) | **Fails closed**: a hit blocks the whole extraction (422); a rail error is 503; enabled without the package, the server refuses to start | Runs the same deterministic detectors as the guard | Built |
| Provider rules | Never falls back between providers. Azure needs `AI_CLOUD_ENABLED=1`, `AI_CLOUD_SYNTHETIC_DATA_ONLY=1` and an allow-listed host. The local model is loopback-only, hash-pinned, keyed, offline, with no web UI or prompt logging | `backend/app/config.py`, `ai/local_provider.py`, `ai/azure_chat.py`, `scripts/start_local_llm.py` | `tests/privacy/test_config_hardening.py`, `tests/ai/test_local_provider.py`, `tests/services/test_kernel_factory.py` | Server refuses to start | — | Built |
| Template note, not model prose | Every note sentence is built from a stored field and cites it; urgency comes from the latest rules run | `backend/app/ai/note.py` | `tests/ai/test_note_regressions.py` | — | — | Built |
| Deterministic encounter summary | Case-level summary from every stored source; no model call; grounding gate; unchecked values kept out of prose | `backend/app/ai/encounter_summary.py` | `tests/ai/test_encounter_summary*.py`, `test_encounter_eval.py` (47 synthetic cases, every safety metric must pass) | CI fails | Synthetic corpus in mock mode; not evidence of clinical accuracy | Built |
| Read-only assistant tools | Six Semantic Kernel functions bound to one visit by the server, no parameters, no writes, access and consent checked per call; only `services/kernel.py` invokes a kernel | `backend/app/services/visit_tools.py`, `kernel.py` | `tests/services/test_visit_tools.py`, `test_kernel.py`, `tests/privacy/test_boundary.py` | Call refused | The assistant screen is not built yet (Phase 5) | Partial |
| Medical image descriptions | Describes visual findings, never diagnoses; field-level guard; confidence shown as a label; acknowledgement before sign-off; ECG never sent; cloud backends need consent and a synthetic-data attestation | `backend/app/ocr/medgemma.py`, `image_service.py` | `tests/test_medgemma.py` | Malformed output is recorded as failed, nothing guessed | **Smoke-tested only**: MedGemma described a drawn picture as a normal chest X-ray at confidence 0.95 | Partial |
| OCR verification | Low-confidence words re-read at 2× zoom; disagreements kept as disputes; RxNorm match; reference-range flags; a human confirms each value | `backend/app/ocr/verify.py`, `rx.py`, `quality.py` | `tests/ocr/` | Dispute shown, never auto-resolved | No real-document evaluation yet (`docs/15` plan not started) | Partial |
| Lab timeline | Checked values only (identity-checked documents, values a person confirmed or corrected), across linked visits only with `link_visits` consent; dated by the printed report date read day-first (machine-read and marked as not checked); compared as higher, lower or same only between plain numbers in the same unit and when the date order agrees with the upload order; gaps named; no urgency (docs/22) | `backend/app/lab_timeline/` | `tests/lab_timeline/`, `tests/review/test_facility.py` | Wrong role 403; consent refusal audited; unreadable dates and unchecked values become gaps, never guesses | Report dates are not checked by a person; no re-test interval flag (no sourced interval); no unit conversion; synthetic reports only | Built |
| Voice read-back | Every extracted candidate needs an explicit decision by the case reviewer; silence or a timeout never confirms; transcripts are untrusted text | `backend/app/voice/readback.py`, `service.py` | `tests/voice/` | — | Hindi and Odia accuracy not evaluated with native speakers | Built |

## 3. Consent, privacy and data

| Control | What it does | Code | Pinned by | On failure | Limits | Status |
|:---|:---|:---|:---|:---|:---|:---|
| Layered consent by purpose | `triage`, `ai_assist`, `voice_cloud`, `link_visits`; the dependent purposes need `triage`; omitted means declined; versioned notice recorded with each event | `backend/app/consent.py`, `consent_notice.py` | `tests/privacy/test_consent.py`, `test_consent_notice_facts.py` | Processing refused | Hindi and Odia notices are unreviewed drafts (warning shown; `docs/13`) | Partial |
| Consent re-checked after slow work | AI and voice results are discarded if consent changed while they ran | `backend/app/privacy/gateway.py`, `backend/app/voice/service.py` | `tests/privacy/test_gateway.py`, `tests/voice/test_consent_precheck.py` | 409, result discarded | Audio already sent to a cloud engine cannot be recalled | Built |
| Data minimisation | Audio is held in memory for the request only, never written or logged; OCR needs an explicit retention setting | `backend/app/voice/service.py`, `backend/app/config.py` | `tests/voice/test_api.py`, `tests/privacy/test_config_hardening.py` | Server refuses to start without `OCR_RETENTION_DAYS` | Retention enforcement and erasure are not built | Partial |
| Synthetic data only | Demo authentication runs only in `development`/`test`; cloud switches need an explicit synthetic-data acknowledgement | `backend/app/config.py` | `tests/privacy/test_config_hardening.py` | Server refuses to start | A policy, not a technical guarantee | Built |
| Cloud vendor limits | Sarvam base URL allow-listed; cloud speech needs `voice_cloud` consent | `backend/app/config.py`, `voice/engines.py` | `tests/privacy/test_sarvam_base_url.py` | Refuses to start | Sarvam may retain content up to 30 days unless the organisation opts out | Built |

## 4. Access, identity and audit

| Control | What it does | Code | Pinned by | Status |
|:---|:---|:---|:---|:---|
| Server sessions | 256-bit tokens, only their SHA-256 stored; idle and absolute timeouts; revocation takes effect on the next request | `backend/app/sessions.py` | `tests/privacy/test_sessions.py` | Built |
| OpenID Connect | Asymmetric algorithms only; nonce; authorization code with PKCE in the frontend | `backend/app/oidc.py`, `frontend/src/lib/oidc.ts` | `tests/privacy/test_oidc.py` | Built (no production identity provider configured) |
| Roles and case access | Unknown and forbidden cases return the same 404; denials are audited by reason without telling the caller | `backend/app/consent.py`, `security_events.py` | `tests/privacy/test_authz.py` | Built |
| Facility isolation | Accounts see only their facility codes; with no mapping the server refuses to start unless isolation is switched off knowingly | `backend/app/config.py`, `consent.py` | `tests/review/test_facility.py` | Built |
| Rate limits | Patient-code entry and sign-in limited per principal and per source, with temporary blocks | `backend/app/ratelimit.py` | `tests/privacy/test_rate_limit.py` | Built (single host) |
| Patient identity binding | A patient login links to a record only through a single-use, 15-minute code from the visit's ANM | `backend/app/patient_identity.py` | `tests/privacy/test_patient_identity.py` | Built |
| Hash-chained audit log | Append-only rows (database triggers) with a hash chain checked by `verify_chain` | `backend/app/audit.py`, `database.py` | `tests/privacy/test_audit.py` | Built: **tamper-evident, not immutable** |
| Append-only clinical records | Triage runs, review events, transcriptions, OCR documents and fields, images and sessions reject in-place edits and deletes | `backend/app/database.py` | `tests/privacy/test_migration_integrity.py`, `test_migrations.py` | Built |
| Fail-closed configuration | Unsafe or incomplete settings stop the server at start-up instead of running degraded | `backend/app/config.py` | `tests/privacy/test_config_hardening.py` | Built |
| Browser hardening | No session token readable by page scripts; every call through the same-origin proxy; no WebSocket endpoint; no credential in a URL | `frontend/src/app/api/`, backend routes | `tests/privacy/test_remediation_guards.py` | Built |

## 5. Engineering process

| Control | What it does | Where | Status |
|:---|:---|:---|:---|
| CI on every pull request | Backend suite with migration checks, frontend tests, TypeScript and ESLint, gitleaks secret scan | `.github/workflows/ci.yml` | Built |
| Skip gate | Fails a green run with unexpected skips, a required safety module that did not run, or fewer than `MIN_EXECUTED` tests | `backend/scripts/ci_skip_gate.py`, `tests/ci/test_skip_gate.py` | Built |
| Branch protection | Required checks plus one approving review before merge | GitHub settings | Built |
| Agent harness | Hooks and settings that stop coding agents from broad git operations, secrets, the shared database and unapproved safety changes | `.claude/`, `HARNESS.md`, `tests/ci/test_agent_harness.py` | Built |
| Language review | Hindi and Odia wording tracked until native-speaker and clinical review | `docs/13_Language_Review_Checklist.md` | Partial (reviews pending) |

## 6. Planned (not built: never present as working)

| Control | Phase | Why it matters |
|:---|:---|:---|
| Evidence-locked protocol retrieval: each fired rule shows the checked source passage, quoted word for word; abstains when no checked passage exists (feature #40, the PRD's "CRAG" layer) | 4 | Lets the clinician verify the basis of each flag |
| Assistant screen with closed actions and a quote gate on protocol sentences | 5 | The tools exist; the screen does not |
| `make safety-eval` scorecard with denominators (feature #31) | 6 | One reproducible safety slide |
| HASSUM uncertainty escalation (feature #35) | None assigned | Claimed in the PRD's defence layers but **not implemented**; today grounding failures and MAKER disagreement route to a human instead |
| Retention enforcement, erasure, grievance flow, breach procedure, emergency bypass, DPIA | Not scheduled | Required before real patient data (`ETHICS.md`) |
| Bias and fairness evaluation across language, age, sex and literacy | Not scheduled | No evidence yet that performance is even across groups |
| Clinical validation | Not scheduled | Required before any real patient |

## 7. Known limits

- Synthetic data only. No clinical validation, no real-document OCR evaluation, no native-speaker review of Hindi or Odia.
- PII redaction is heuristic; 5 misses are documented. The output guard and keyword rules are regular expressions.
- The local model (Qwen3-4B, general-purpose) found 0 of 15 red flags on its own and kept 1 of 4 denied symptoms in a
  one-pass test; the keyword layer and the always-asked red-flag screen cover the first, human review the second.
- MedGemma was overconfident on a drawing (above). Image descriptions are never findings.
- The audit log is tamper-evident, not immutable. One host, one SQLite database. Shared, password-less demo accounts.
- RED escalation is shown on screen only.

Model-by-model results are in `MODEL_CARD.md`; ethics, law and patient rights in `ETHICS.md`; security in `SECURITY.md`.
