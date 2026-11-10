# Final security, privacy and clinical-safety remediation: audit and plan

> Branch `integration/semantic-kernel-remediation` at 82a6352, 15 commits on top of `main` 4b44cc8. Prepared 2026-11-10.
> **Status: plan, before implementation, revision 2 after the llm-council review (§6).** Research prototype, non-diagnostic. This document claims no production readiness, clinical validation, DPDP compliance or data residency.
> **Method:** three independent read-only audits, one each for authorization, migrations/secrets/CI, and voice/summary/plugins. Each finding was verified against the code with file:line evidence or a probe run outside the repo. Synthetic data only. The previous remediation report was not trusted, and each of its claims was re-checked.

## 1. Repository safety (checked before any change)

| Item | State |
|---|---|
| Working folder | `/Applications/Developer/Hackathon/sehat-ai` on `feat/voice-agent-multimodal` @ 098a00e. 11 untracked WIP paths plus a `frontend/package.json` edit. **Not touched.** |
| Remediation work | Isolated worktree on `integration/semantic-kernel-remediation`, clean |
| `main` | 4b44cc8, protected: 3 required checks, 1 approving review, enforced for admins, no force-push or delete, conversations must be resolved |
| Other branches | `fix/p0-1-lab-parser`, `fix/patient-document-upload` and `fix/patient-intake-workflow` are fully merged (0 ahead). `feature/gemini-live-duplex-followup` and `feature/semantic-kernel-plugins` are superseded by this branch. `ci/basic-workflow` is draft PR #13 (blocked by protection, as designed). |

## 2. Findings matrix

> **Caveat: read this first.** Because of CI-1, CI has not been running 149 safety tests. Every "verified safe" claim below was verified **locally**: the full suite on a development machine with all optional dependencies, plus the audit probes. None of them is CI-verified until step 1 lands.

Severity: **P0** = security, privacy, clinical-safety or data-loss risk, or a safety check that can pass while wrong. **P1** = correctness or coverage gap. **P2** = hygiene or documentation.

| ID | Issue | Sev | Status | Evidence | Fix (test first) |
|---|---|---|---|---|---|
| CI-1 | CI is green while **149 safety tests are never collected**: every voice-consent test, the encounter-summary suite and the verification-queue suite | P0 | defect | `tests/voice/test_api.py:19` has a module-level `importorskip("silero_vad")`. `tests/ai/test_encounter_summary.py:16-17` and `tests/review/test_verification_queue.py:12-13` import fixtures from it. Simulating CI collects 1295 of 1444 tests. | Move the shared fixtures into a helper with no importorskip. Fake the VAD only in the consent tests. Add a CI gate that fails on any skip whose reason is not allowlisted, or if a required-safety test ID is missing or skipped, or if fewer tests are collected than expected. |
| V1 | The "device voice" read-back can use a **cloud** browser voice (Chrome "Google हिन्दी", Edge "Online (Natural)"), sending the spoken health value to Google or Microsoft without `voice_cloud` consent, while labelled "this device's voice" | P0 | defect | `frontend/src/lib/useMatchingVoice.ts:17` never checks `localService` | Choose only voices with `localService === true`, otherwise "unavailable". Unit test with a non-local voice. |
| M-3 | A database from **newer code** (`user_version` > SCHEMA_VERSION) is accepted, and the step-13 repair writes into it | P1 (data integrity) | defect | probe E: version 99 is accepted and repaired | Refuse it before any change (`SchemaMismatchError`), and leave the file untouched |
| M-1 | The step-13 repair checks only that a table **name** exists. A wrong-schema `encounter_summaries`, or a missing review table, index or append-only trigger, is accepted. | P1 | gap | probes A, B, D | Validate the schema after migrating: expected columns, indexes and triggers for tables created or rebuilt by steps 13 and 14 (and the core append-only tables). A mismatch fails closed with rebuild instructions. |
| M-2 | `user_version` is trusted for every step except 13 | P1 | gap | probe F: version 14 with no step-14 schema is accepted | The same validator |
| M-4 | The repair is recorded in the application log only | P2 | gap | `database.py` repair log | Add an audit record (`schema_repaired`, step and count only) |
| S1 | **Negation lost**: "cough, no fever" with the model saying "negated" grounds as *present* with a `negation_conflict` flag. The summary drops the flag and writes "Patient reports: fever". A clinician reads a denied symptom as present. | **P0** (raised by the council) | defect | `encounter_summary.py:330-339` drops `flags`; `grounding.py:96-103` | Carry the negation and temporal conflict flags into the Fact. A flagged AI symptom stays out of the prose and appears in "withheld", with the reason, until a reviewer decides. Test with the exact sentence. |
| S3 | The gate does not check polarity: a "Patient reports:" sentence citing a `:denied` fact would pass. Each sentence gets one tag check, however many facts it cites. | P2 | gap | `gate()` lines 809-838 | The gate blocks a polarity mismatch and checks tags per fact. Test with a hand-made bad sentence. |
| S4 | No test that denied symptoms render as "Patient denies" and never as present | P1 | test gap | the comment at `test_encounter_summary.py:192` is not asserted | Add the test |
| S2 | No temporal attribute: AI-extracted past symptoms and stopped medicines read as current. "Patient reports" is hard-coded even when a caregiver or ANM spoke. | P1 (attribution, fixed here); temporal is a **known residual**, not fixed | gap | Fact model; lines 753-755 | Neutral attribution: "Reported at intake:". Temporal: the extraction schema has no time field, so this is **documented as a residual**; `temporal_reference` flags are carried where they exist (as S1). |
| K1 | The kernel registers Azure whenever Azure credentials exist, even with `AI_CLOUD_ENABLED` unset, and `/health` then says "configured". Plan §7 item 4 is not met. | P1 | defect | `kernel.py:21-30`; probe | Register only when `ai_cloud_enabled` and configured. Test with credentials set and the flag off. |
| S-1 | `repr(Settings)` exposes `jwt_secret_key` and `azure_openai_api_key` | P1 | defect | `config.py:66,69` | `field(repr=False)`. Test with sentinel values. |
| V2 | The browser does not re-check consent just before uploading, and the server reads the whole audio body before the consent check. Audio is never stored or forwarded, but "Nothing was sent" is untrue. | P1 | gap | `routes/voice.py:106-111`; page `upload()` | Check consent read-only on the server **before** reading the body. Re-check in the client before `postAudio`, and fix the message. Test: withdrawn consent plus an oversized body gives 403, not 413. |
| F3 | IDOR coverage gap: AI extraction/field/notes, counterfactuals, summary review, voice by id/readback/TTS, medical images and the registry have no cross-case substitution test (the code is safe) | P1 | test gap | `test_facility.case_requests` covers 22 paths | Extend `case_requests`; add cross-case id substitution tests |
| F7 | Voice transcript list/get skip the second consent check | P2 | gap | `voice/service.py:360-375` | Read inside `read_transaction` with a re-check (as OCR does) |
| F2 | Facility isolation is off unless `ACCOUNT_FACILITIES` is set (the default in `.env.example`) | P1 | gap | `auth.py:60-62` | **Owner decision D-A** (§5). The council recommends **failing closed**: refuse to start without `ACCOUNT_FACILITIES` unless an explicit `ALLOW_UNSCOPED_DEMO=true` is set, and ship a scoped `.env.example`. |
| F4 | A supervisor sees clinical triage input/result and the body map, which contradicts docs/11's "metadata only" | P2 | policy | probe | **Owner decision D-B** |
| F5 | docs/11 says only supervisors read the audit log; MOs receive audit rows in case review | P2 | doc defect | `review_queue.py:487` | Fix the doc to match (MO sees actions on their own case) |
| F6 | Access denials and patient-code `lookup` are not audited; the lookup has no rate limit | P2 | gap | `patient_registry.py:178` | Audit `patient_lookup` with the outcome only, never the code |
| S-2 | The JWT algorithm and secret strength are not validated at startup (fails closed at login) | P2 | gap | probe | Allow only HS256/384/512, and require a secret of at least 32 bytes |
| S-3 | No `jti` or revocation; logout only deletes the cookie; rotation is ignored if `JWT_SECRET_KEY` is set in the process environment | P2 | limitation | `auth.py` | Script message plus documentation. Revocation is a residual risk. |
| S-4 | CORS `allow_credentials=True` and `*` are not rejected (cookies are unused by the backend) | P2 | hygiene | `main.py:95-101` | `allow_credentials=False`; reject `*` |
| V3 | The Next.js proxy has no body cap and does not forward the abort signal | P2 | gap | `api/backend/[...path]/route.ts:26` | Forward `request.signal`; cap the body |
| V4 | Missing tests: withdrawal during local-engine processing, same-key retry after withdrawal, no transcript in logs | P2 | test gap | — | Add the tests |
| K2 | Raise-only harness test covers only 2 of 9 pairs (the guard test covers all 12) | P2 | test gap | `test_kernel.py` | Covered by the guard test; nothing to do |
| CI-2 | The guardrails suite (45) is skipped in CI because `nemoguardrails` is optional | P2 | acceptable | `test_guardrails.py:16` | Allowlist the skip reason, visibly |
| CI-3 | Actions are pinned by tag, not by SHA | P2 | hygiene | `ci.yml` | Pin to SHAs |
| F1 | **All patients share one demo account**: any patient login can read another patient's case if it knows the case id | **P0, NOT FIXED** (residual) | documented limitation | probe; docs/17 §8 | **Cannot be fixed here**: it needs a real identity provider. Real use is already blocked (`config.py` refuses to start outside dev/test). Recorded as a residual and on the reviewer checklist. **This is the item that stands between "prototype" and "pilot with a real identity provider".** |
| F8 | Unauthenticated `/health` shows table names and provider status | P2 | hygiene | `routes/health.py` | Out of scope here; listed as residual |

**Verified safe**, with evidence in the three audit reports:
- Facility isolation returns the same 404 across 22 paths, and nothing is written.
- Queue, escalation and governance scope can only narrow, never widen.
- JWT: the algorithm is pinned; `exp`, `sub` and `role` are required; the username must match `sub`; the scope cannot be widened.
- Demo authentication refuses to start outside development and test.
- Downloads are protected against path traversal and checked by hash.
- OCR, AI and summary ids are bound to their case.
- Consent is checked before any Sarvam upload and re-checked after VAD, and a withdrawal mid-run discards the result.
- Audio is never persisted.
- Microphone permission is not treated as consent.
- Read-back candidates are never auto-confirmed.
- Left/right comes from a fixed list.
- An unanswered question never becomes a negative.
- Unchecked OCR values never reach prose.
- Conflicts are never resolved by picking a side.
- The summary never computes urgency.
- Migration steps are atomic, concurrency is tested, FK restoration and consent rebuilds are safe.
- The session cookie is httpOnly, sameSite=lax, and secure in production.
- No credential appears in any URL, and no WebSocket exists.
- Plugins are not registered at runtime and cannot be reached from any route.

## 3. Plan, in order (each item test-first: failing test → smallest fix → focused tests → full suite)

| Step | Items | Files |
|---|---|---|
| 0 | **Before any code:** send the D4 message to Niranjan (done by the owner), and name the independent reviewer for the PR. That reviewer should be able to judge the clinical-safety changes (S1–S4). | none (process) |
| 1 | CI-1 test restructure + skip gate, CI-2 allowlist, CI-3 SHA pins. **Also:** a meta-test proving the gate fails on a deliberately skipped required-safety test, and a permanent **urgency-authority invariant test**: no AI, kernel, plugin, summary or voice path writes or lowers `urgency` (a static import/call-graph check plus a runtime test that the AI output never changes `triage_results`). The gate runs as a step **inside the existing required "backend tests" job**, so protection is enforced without any branch-protection change. **Checkpoint:** CI on the pushed branch is green, and collects ≥ 1444 tests with no unexpected skips, before step 2 starts. | `tests/voice/helpers.py` (new), `tests/voice/test_api.py`, `tests/ai/test_encounter_summary.py`, `tests/review/test_verification_queue.py`, `scripts/ci_skip_gate.py` (new), `.github/workflows/ci.yml` |
| 2 | V1 local-only device voice, then S1/S3/S4 (P0; moved before migrations) | `frontend/src/lib/useMatchingVoice.ts` (+ a pure helper and node test) |
| 3 | M-3, M-1, M-2 schema validation, M-4 audit record | `backend/app/database.py`, `tests/privacy/test_migration_reconcile.py`, `test_migration_integrity.py` (new) |
| 4 | S2 neutral attribution (S1/S3/S4 moved to step 2) | `backend/app/ai/encounter_summary.py`, `tests/ai/test_encounter_summary_semantics.py` (new) |
| 5 | K1, S-1, S-2, S-4 | `services/kernel.py`, `config.py`, `main.py`, tests |
| 6 | V2, F7, V4 | `routes/voice.py`, `voice/service.py`, voice page, tests |
| 7 | F3, F6 (tests and audit); F2 and F4 **only after D-A and D-B are answered**. Steps 1–6 do not depend on those decisions. | `tests/review/test_facility.py`, `patient_registry.py`, `audit.py` |
| 8a | F5 doc fix (in step 7's commit) |
| 8b | Docs, one commit each: the S-3 and F1 residuals, the consent lifecycle, migration recovery, the CI test matrix | docs/11, the integration plan, this file |

**Backward compatibility:**
- The schema validation can refuse a corrupted local database that used to "work". That refusal is intended, and the error gives rebuild instructions; the data is synthetic.
- The summary text changes from "Patient reports:" to "Reported at intake:" (tests updated); the API shape is unchanged.
- No migration is renumbered, and no table is changed.

**Commits and rollback:**
- Each step is one or more separate commits on `integration/semantic-kernel-remediation`, staged by exact path, and each can be reverted on its own.
- The governing prompt allows only this one branch, so the commits go into a single PR. The reviewer can review them commit by commit.
- If the skip gate gives false failures, revert only the gate step; the restored tests stay.

**Risks:**
- Over-strict schema validation could reject legitimate older databases. Mitigation: tests on databases built by every historical step list.
- The CI skip gate could be brittle. Mitigation: the allowlist and the required test ID list live in one file, and changes to them are reviewed.

## 4. Merge policy

Nothing is merged by the implementer. `main` requires CI to pass on the latest commit **and an approving review by someone other than the author**, enforced for admins. The remediation PR therefore needs an independent reviewer (a teammate). Protection will not be weakened.

## 5. Decisions for the owner

These decisions gate step 7 and the PR; steps 1–6 can go ahead without them.

**Owner decisions, 2026-11-10:**
- **D-A = fail closed.**
- **D-B = metadata only for supervisors.**
- **Reviewer = another teammate** (name to be confirmed before the PR).
- **Implementation approved** for steps 1–6, with step 7 now unblocked. No PR until Niranjan is informed.

- **D-A:** facility isolation when `ACCOUNT_FACILITIES` is unset. Proposal: a startup warning in development, unchanged in tests.
- **D-B:** what supervisors may see. Proposal: clinical triage fields only for the MO or creator, and metadata for supervisors, as docs/11 says.
- **D-C:** the remediation PR, once Niranjan has been informed (earlier D4) **and the reviewer from step 0 is named**.

## 6. llm-council review (2026-11-10)

Five advisors (Contrarian, First Principles, Expansionist, Outsider and Executor) each reviewed this plan independently, then five anonymised peer reviews were done and a chairman summarised them. Changes adopted:
- S1 raised to P0.
- F1 labelled plainly as not fixed.
- The CI-1 caveat moved to the top of §2.
- Step 0 added: inform Niranjan and name a qualified reviewer.
- An urgency-invariant test and a meta-test for the skip gate added to step 1, enforced through an existing required check.
- A checkpoint added after step 1.
- Step 7 gated on D-A and D-B; step 8 split.
- A fail-closed F2 proposal.
- A rollback note.

Residual raised by the council: **the semantics of the negation and temporal fixes need review by a clinician or domain expert.** Engineering tests cannot provide clinical validation, and none is claimed.

## 7. Implementation log

**Step 1 (CI-1, CI-2, CI-3, urgency invariant)**
- **Fix chosen:** CI now installs Silero VAD (`backend/requirements-vad.txt`: silero-vad, onnxruntime and CPU-only torch). The consent, summary and queue suites therefore run against the **real** VAD, as they were written to, so no fake VAD was added.
  - The CPU torch wheel is installed on its own with `--index-url`, so no other package resolves from the PyTorch index.
- **`backend/scripts/ci_skip_gate.py`** runs as a step in the required "backend tests" job. It reads pytest's JUnit XML and fails on any of:
  - a skip whose reason is not allowlisted. Allowlisted: optional guardrails, opt-in live tests, and the documented PII misses.
  - a skipped or missing required safety module;
  - fewer than `MIN_EXECUTED` tests run.
- **Meta-tests:** `tests/ci/test_skip_gate.py` reproduces the CI-1 module-level `importorskip` and checks that the gate fails on it.
- **Invariant:** `tests/privacy/test_urgency_authority.py` pins, across the whole app:
  - the allowlisted callers of the rules engine;
  - `case_triage.py` as the only writer of `triage_runs`;
  - that the recorded urgency is the engine result;
  - that the AI, voice, OCR, services and privacy packages never import the run writer or the review queue.
- **Actions pinned to commit SHAs.**
- **Verified in a fresh Python 3.11 environment with exactly the CI install:**
  - 1393 passed, 15 skipped (all allowlisted), 5 xfailed. The skip gate passes.
  - The restored suites now run: voice API 47, encounter summary 31 (+10 transparency), verification queue 12.
  - **The workflow only runs on pull requests**, so the GitHub run happens when the PR opens.

**Step 2a (V1)**
- `frontend/src/lib/deviceVoice.ts` selects only `localService` voices for the selected language; a cloud browser voice is never used.
- Used by `useMatchingVoice`, which covers both read-back and the consent read-aloud. Tests in `deviceVoice.test.ts`.

**Step 2b (S1–S4, encounter summary semantics)**
- **S1:**
  - Unreviewed AI symptom, medication and red-flag facts now carry their grounding flags (`negation_conflict`, `negation_unsupported`, `past_history_cue`, `temporal_reference`).
  - A flagged fact stays in the tables and out of the text, and is listed in `withheld` as `negation_or_time_uncertain` until a person decides it.
  - The exact case "Cough, no fever." with the model's cross-clause negation now writes cough only.
- **S3:** the gate blocks `polarity_mismatch` (a "reported" sentence citing a denied fact, or the reverse) and any flagged fact in prose. The unchecked tag is now counted per cited fact.
- **S4:** a test asserts that a denied symptom renders only as denied.
- **S2:** "Patient reports/denies" is now "Reported/Denied at intake", which makes no claim about who spoke.
  - Temporal attributes remain a residual: the extraction schema has no time field.
- **Schema** `encounter-summary-2026-11-10.3`, so older stored summaries go stale and are rebuilt.
- **Frontend wording** added for the two new reason codes.
- **Verified:** backend 1446 passed / 14 allowlisted skips; frontend summary tests 14/14.
- **Clinical review of the wording is still required.**
