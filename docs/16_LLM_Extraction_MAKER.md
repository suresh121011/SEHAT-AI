# 16 — LLM Extraction, MAKER Voting & Source-Linked Notes (Phase 6)

> **MAKER, in one line:** ask the model the same question several times (3 by default). A value is trusted only as far as the independent answers agree. Every disagreement goes to a human. (Adapted from MAKER, arXiv:2511.09030, in a basic form.)
>
> **Status:** research prototype, not a clinically validated device. Non-diagnostic. **Synthetic data only.** No live LLM has been run: Azure OpenAI credentials are not available, so every check uses the **fake provider**. The fake is a deterministic keyword extractor, **not an LLM**.

## 1. What Phase 6 does

The safety model from Phases 2–3 is unchanged:

- **Rules set urgency.** The deterministic engine (`app/rules`) produces the base urgency.
- **The model only extracts and summarises.** It may suggest raising urgency. Any suggestion goes through `app.rules.enforce_raise_only()` and can never lower the deterministic level.
- **A human reviews every field**, one field at a time. There is no bulk accept.
- **Every value links to its source:** a verbatim quote, the segment it came from, and the character offsets back into the stored raw transcript where possible.

```
POST /cases/{id}/ai/extractions
  inputs.py      typed intake text + completed voice transcripts (en; hi/or only via local IndicTrans2, flag-gated)
                 → segments S1..Sn, each with a source reference. Reviewed OCR values are added as-is (never sent to a model).
  gateway        privacy.gateway.submit_structured: T1 consent(ai_assist) → redact each segment (fail closed)
                 → T1b consent unchanged → provider passes → T2 consent unchanged → persist + audit
  maker.py       3 concurrent passes (temperature 0.1/0.2/0.3) → strict schema validation (invalid = abstain)
                 → grounding.py (quote ⊂ segment, number ⊂ quote) → asymmetric vote
POST /cases/{id}/ai/fields/{field_id}/review     accepted | corrected | rejected | unsure (append-only)
GET  /cases/{id}/ai/reviewed                     view only; triage-form hints; never submits triage
POST /cases/{id}/ai/notes                        server-template note; rules urgency first; enforce_raise_only
```

## 2. Providers: `none | fake | azure`

| `AI_PROVIDER` | What runs | Notes |
|:---|:---|:---|
| `none` (default) | Nothing | AI endpoints return `503 AI_NOT_CONFIGURED`. |
| `fake` | `app/ai/fake_provider.py`, a deterministic keyword/regex extractor | Offline. Labelled `provider_kind: "deterministic keyword extractor (not an LLM)"` in every response, with `synthetic_provider: true`. `AI_FAKE_MODE=demo_disagreement` makes pass 2 disagree on the first measurement, to demonstrate a disputed value. |
| `azure` | `app/ai/azure_provider.py`: Azure OpenAI through Semantic Kernel, using strict JSON-schema structured output | Config-gated. See §7. **Not run live** in this build. |

There is **no fallback** between providers. If every pass fails, the request returns `502 AI_ADAPTER_ERROR`.

**Provider contract** (`app/ai/adapter.py`):
- Input is `RedactedPrompt` only, checked at runtime. A `RedactedPrompt` can only be created by `app.privacy.pii.redact_segments`.
- Output is raw JSON text.
- The **server** validates the output against `ExtractionOutput` (`extra="forbid"`, strict).

The provider prompt (`app/ai/prompts.py`, versioned) passes segments as delimited data and says they are not instructions. This does not prevent prompt injection. The real controls are the strict schema, grounding, and the raise-only boundary.

**Because the fake is deterministic, its three passes always agree.** MAKER voting is therefore exercised only through test perturbations and `demo_disagreement` mode. Agreement figures from the fake say nothing about model reliability.

## 3. Extraction schema (`app/ai/schemas.py`)

The schema contains:
- `chief_complaint`, `onset`, `duration` (text)
- `symptoms[]` (name, negated)
- `measurements[]`: `temp`, `spo2`, `pulse`, `resp_rate`, `bp` (value plus value2), `hb`, `platelets`, `creatinine`, `troponin`, `glucose`, `pain_severity`
- `medications[]`
- `red_flags[]`: `AtpFlag` enum values only
- `urgency_suggestion`

**Every item carries `evidence: [{segment_id, quote}]`.** Fields are required but nullable, so the same models serve both the Azure strict schema (`strict_json_schema`) and server-side validation.

## 4. Grounding and source links

`app/ai/grounding.py` is pure, cannot be bypassed, and runs **before** voting:

- **Quotes:** the quote must occur in the cited redacted segment (case- and whitespace-insensitive).
- **Numbers:** the value, and `value2` where present, must occur as a number in the quote. Number words are parsed with the voice extractor's parser, so "one hundred and forty over ninety" grounds 140/90. "one forty over ninety" does **not**: that parser deliberately never sums "one forty". The value is dropped with reason `value_not_in_quote`.
- **Text values:** every word of 3 or more characters must occur in the quote.
- **Identifier patterns:** a value that matches one is dropped (`pii_pattern_in_output`).
- **Negation is asymmetric.** A mention counts as negated only if the model says so **and** the quote contains a negation cue. A disagreement in either direction keeps the mention present and adds a flag (`negation_conflict` or `negation_unsupported`) for review.
- **Nothing is dropped silently.** Each drop is listed in `dropped: [{field, reason}]`.

**Source references** (`extract.source_ref`):
- `redacted_chars` locate the quote in the stored redacted segment.
- For transcripts, `transcript_chars` map back into the stored raw transcript. This uses the offset map that `redact_segments` builds from the applied redactions. A range touching a redaction token widens to the replaced span.
- These offsets are exact only when Unicode normalisation left the segment unchanged, which is always true for plain ASCII. Otherwise `transcript_chars` is `null` and the segment range is still given.
- Translated segments link to the **sentence** in the original transcript (`original_chars`), because the quote is in the translation.

## 5. MAKER voting (`app/ai/maker.py`)

Voting is asymmetric (council amendment). Agreement is reported as a count, for example `"3/3"`, **never as a confidence number**: passes from one model share its errors.

| Item | Rule |
|:---|:---|
| Values (text, symptoms, medications) | All valid passes agree → `agreed`. A strict majority → `majority`. Otherwise → `disputed`. |
| Critical values (all vitals, Hb, platelets, creatinine, troponin, glucose) | Anything short of unanimous → `disputed`, with `value: null` and the candidates listed (e.g. 140/90 vs 150/90) for a human to enter. |
| Red-flag mentions (alarms) | **Never voted away.** A grounded, non-negated mention from even one pass surfaces: `agreed` if every pass has it, otherwise `disputed_raise` with `priority_review`. |
| Urgency suggestion | Passed to `enforce_raise_only` only if every valid pass gives the same level. Otherwise it is withheld and the candidate levels are shown. |
| Fewer than 2 valid passes | Run is `insufficient_agreement`; no fields are produced. |

An invalid reply abstains and is recorded under `maker.abstentions` with a reason code only. Invalid replies include non-JSON, extra keys, wrong types, and a provider exception.

The docs/09 values "0.95 / 0.5 confidence" are not used. A probability would overstate what agreement between correlated passes shows.

## 6. Review, the reviewed view, and the note draft

- **Review** (`app/ai/review.py`) records `accepted`, `corrected`, `rejected` or `unsure` as append-only events.
  - Each new decision names the event it supersedes; a mismatch returns `409 REVIEW_CONFLICT`.
  - A disputed value has no single value to accept (`422 ACCEPT_REQUIRES_VALUE`): it must be corrected or rejected.
  - Correction text is limited to 200 characters, and identifier patterns are rejected (`422 PII_DETECTED`).
  - Reviewed OCR values stay owned by the document review (`409 REVIEWED_AT_SOURCE`).
  - There is no bulk-accept endpoint.
- **Reviewed view** (`GET /ai/reviewed`) returns accepted and corrected values with their sources, plus `form_hints` that map them to `TriageInput` fields. Red-flag mentions come only as candidates for the red-flag screen.
  - **Nothing is submitted:** a human enters values on the triage form.
- **Note draft** (`app/ai/note.py`) is a **server template** in this phase, not model prose (council amendment):
  - Every claim is built from one stored field and cites its `field_id`.
  - Rejected and disputed fields produce no claim. Disputed values are listed under `needs_human_entry` with their candidates.
  - The output guard (`app/ai/guard.py`) blocks diagnostic, prescriptive, instruction-like or identifier-like text, including reviewer corrections.
  - The summary is at most 500 characters.
  - Fixed disclaimer, `clinical_use_allowed: false`, `requires_sign_off: true`.
  - Urgency:
    - **Latest case triage run exists:** the deterministic urgency stands, and `enforce_raise_only(result, suggestion)` gives the final level. A raise is shown as `raise_requires_human_action`. A downgrade is refused with the engine's reason.
    - **No triage run:** `"not determined by the rules engine — human triage required"`.
  - **No AI endpoint writes `triage_runs`.** A static test checks this.

## 7. Enabling Azure OpenAI later (env only)

Set all of the following, then restart. Each missing or wrong value refuses to start with an exact message, and values are never echoed:

```
AI_PROVIDER=azure
AI_CLOUD_ENABLED=1                     # redacted case text leaves this machine
AI_CLOUD_SYNTHETIC_DATA_ONLY=1         # acknowledgement: redaction has known misses (§8); synthetic data only
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/   # or *.cognitiveservices.azure.com; https, no path/port/credentials
AZURE_OPENAI_DEPLOYMENT_NAME=...       # a deployment that supports structured outputs (json_schema, strict)
AZURE_OPENAI_API_VERSION=...           # an API version that supports structured outputs
AI_MAKER_PASSES=3                      # 3–5
AI_TIMEOUT_S=30
```

Before relying on it, also:

- Run `GET /api/v1/ai/capabilities`, and one extraction with synthetic text.
- Check that the deployment accepts `temperature`. Some reasoning models do not. If yours doesn't, every pass fails and you get a 502, never a silent fallback.
- Keep the 5 known redaction misses in mind (`tests/privacy/test_pii.py` xfails).

The endpoint host allowlist is in code (`AZURE_OPENAI_HOST_SUFFIXES`). Widening it is a reviewed change.

## 8. Limits and known gaps (honest)

- **Not run against any real LLM.** The Azure path is covered by one mocked Semantic Kernel contract test (strict `response_format`, temperature per pass, only redacted text sent).
- **Redaction misses:** 5 known name/DOB misses (lowercase or uncued Indian names). That is why real patient text must not go to a cloud model and Azure requires `AI_CLOUD_SYNTHETIC_DATA_ONLY=1`.
- **Romanised Hinglish:** it passes the Latin-script check, but both redaction and the fake handle it poorly. **Untested.**
- **Redaction removes clinical data:**
  - The full-date (DOB) pattern also redacts onset dates, so "since 28/09/2026" becomes `[DATE_REDACTED]` and cannot be extracted.
  - A run of 8 or more bare digits fails the whole request closed, e.g. "glucose 245 312 280" → `422 PII_DETECTED`. This fail-safe is accepted as is.
- **Spoken numbers:** see §4. "one forty" is dropped, not guessed.
- **Withdrawing consent after storage:** AI rows are kept but no longer served, because every AI endpoint re-checks `triage` and `ai_assist`. There is no purge or tombstone path yet. Deletion was deferred in Phase 3.
- **Prompt injection:** instruction-like intake is flagged `possible_instruction_text`. Grounding and raise-only bound the damage, but no detector is complete.
- **IndicTrans2 (P2):**
  - The code path is tested with a mocked translator only. The model (`ai4bharat/indictrans2-indic-en-dist-200M`, MIT) is **gated** on Hugging Face, so it was not downloaded or run here.
  - Translation quality on clinical speech is unevaluated.
  - Transliterated names in translations may evade redaction.
- **Follow-up questions:** English only. Hindi and Odia wording needs review first (docs/13).
- **Not built:**
  - an LLM-written note (template only)
  - HASSUM semantic entropy
  - CRAG / GraphRAG
  - NeMo Guardrails
  - SNOMED / RxNorm codes in the note
  - a frontend (Phase 8)

## 9. Data, consent and audit

- **Consent:**
  - Extraction needs `ai_assist`, which implies `triage`. Every read, review and note re-checks both.
  - **Roles:** the ANM who created the case, or a medical officer.
- **Storage** (migration 6, append-only by trigger):
  - `ai_extraction_runs`: redacted segments, skipped sources, drops, abstentions, urgency vote, flags
  - `ai_fields`
  - `ai_field_review_events`
  - `ai_note_drafts`

  **Raw intake text is never stored.** Migration 7 adds a nullable `triage_runs.input_json`, so counterfactuals can be recomputed for new runs. It holds rules-engine input only, with no identifiers.
- **Audit** records counts and enums only: `ai_extraction_recorded`, `ai_field_reviewed`, `ai_note_drafted`, plus the gateway's `pii_redacted`, `ai_output_returned`, `ai_output_discarded` and `ai_request_blocked`. No values, quotes or text are recorded. The e2e script checks this.

## 10. Missing information, follow-up questions, counterfactuals (P1, deterministic)

- **Missing information.** `app/rules/required_fields.py` defines the per-scenario lists. OPD, maternal and NCD follow the architecture §12; other scenarios use the core vitals.
  - The red-flag screen is **always** listed first and never inferred from text.
  - Disputed or rejected values count as missing.
- **Follow-up questions.** `app/ai/question_bank.py` holds English questions only, at most 5, danger signs first. For `hi` and `or` it returns `not_available_pending_review`.
- **Counterfactuals.** `app/rules/counterfactual.py` changes one input at a time and re-runs the pure engine. For each recorded vital it reports the nearest value at which the engine's urgency changes, searching within the engine's own input bounds. It also covers removing each red flag and leaving the red-flag screen incomplete.
  - No new thresholds are introduced.
  - The note includes counterfactuals for the latest run. Runs recorded before migration 7 report `unavailable`.

## 11. Endpoints

| Method | Path | Notes |
|:---|:---|:---|
| GET | `/ai/capabilities` | Provider, kind, cloud, MAKER passes, translation state |
| POST | `/cases/{id}/ai/extractions` | `{idempotency_key, intake_text?, include_voice?, include_ocr_reviewed?}` → 201. A retry with the same key never re-runs the provider. |
| GET | `/cases/{id}/ai/extractions[/{extraction_id}]` | Run view, with `missing_information` and `follow_up_questions` |
| POST | `/cases/{id}/ai/fields/{field_id}/review` | `{outcome, corrected?, supersedes?}` |
| GET | `/cases/{id}/ai/reviewed` | View only |
| POST / GET | `/cases/{id}/ai/notes` | `{extraction_id}` → note draft / list |
| POST | `/triage/counterfactuals` | Stateless; `TriageInput` body |
| GET | `/cases/{id}/triage/runs/{run_id}/counterfactuals` | From the stored input of that run |

**Error codes:**

| Code | Meaning |
|:---|:---|
| `503 AI_NOT_CONFIGURED` | No provider configured |
| `502 AI_ADAPTER_ERROR` | Every provider pass failed (no fallback) |
| `422 AI_NO_INPUT` | Nothing to extract |
| `422 PII_DETECTED` | Identifier pattern after redaction, or in a correction |
| `422 AI_INPUT_UNSUPPORTED_LANGUAGE` | Text is not English (Latin script) |
| `409 CONSENT_WITHDRAWN` | Consent changed while the request was running |
| `403 CONSENT_REQUIRED` | Consent for this purpose is not in effect |
| `409 REVIEW_CONFLICT` | The field was reviewed by someone else |
| `422 ACCEPT_REQUIRES_VALUE` | A disputed value cannot be accepted |
| `422 CORRECTION_REQUIRED`, `CORRECTION_INVALID`, `CORRECTION_UNEXPECTED` | Correction missing, wrong type, or not allowed for this outcome |
| `409 REVIEWED_AT_SOURCE` | OCR value; change it in the document review |

## 12. Verification (2026-10-03)

- **Backend:** `929 passed, 10 skipped (opt-in live), 5 xfailed`, up from the Phase 5 baseline of 831 passed. The 99 Phase 6 tests in `backend/tests/ai/` cover:
  - grounding, voting, guard and redaction table tests on realistic redacted inputs
  - adapter contract, config refusals, mocked Azure contract
  - consent withdrawal mid-flight (`ai_assist` and `triage`): nothing persisted
  - raise-only: a GREEN suggestion on a rules-RED case → RED, refused; a unanimous RED raise on YELLOW → shown, not recorded; a split vote is withheld
  - `triage_runs` unchanged by AI endpoints, plus the static no-write check
  - adversarial fake: fabricated values dropped, alarms kept
  - P1 pure functions and endpoints; mocked translation
- **HTTP walkthrough:** `backend/scripts/e2e_ai_check.py` passed **16/16** against a live uvicorn server with `AI_PROVIDER=fake AI_FAKE_MODE=demo_disagreement`. Steps: provider label → consent → extraction (redacted, located quotes, red-flag mention) → disputed BP → review → reviewed view → rules triage → note (GREEN → RED raise shown, claims cited, sign-off required) → withdrawal → 403 → audit has no values → chain verifies.
- **Not verified:**
  - any real LLM
  - the real IndicTrans2 model
  - extraction accuracy on real or realistic clinical text (no evaluation set; the fake's numbers would not be model accuracy anyway)
  - any UI

## 13. Decisions (council, 2026-10-03)

An llm-council review (5 advisors, anonymised peer review, chairman) of the plan led to these decisions:
- Asymmetric voting: alarms are never voted away.
- Agreement counts instead of confidence numbers.
- No silent blanks: candidates and drop reasons are always shown.
- A server-template note in P0.
- A redacted→raw offset map.
- An adversarial fake mode, so grounding tests can actually fail.
- `AI_CLOUD_SYNTHETIC_DATA_ONLY` as an explicit gate.
- No bulk accept.
- Every AI read re-checks consent.
- Documented limits: Hinglish, onset dates, no purge path.

Rejected: dropping `ai_fields` and `triage_runs.input_json` (both were in the agreed scope), and FHIR/LOINC hooks (out of scope).
