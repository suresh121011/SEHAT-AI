# 16 — LLM Extraction, MAKER Voting & Source-Linked Notes (Phase 6)

> **MAKER, in one line:** ask the model the same question several times (3 by default) and show how far the answers agree. Every disagreement goes to a human. The passes are not independent (one model shares its own errors), so agreement is a filter, not proof. (Adapted from MAKER, arXiv:2511.09030, in a basic form.)
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

## 2. Providers: `none | fake | azure | local`

| `AI_PROVIDER` | What runs | Notes |
|:---|:---|:---|
| `none` (default) | Nothing | AI endpoints return `503 AI_NOT_CONFIGURED`. |
| `fake` | `app/ai/fake_provider.py`, a deterministic keyword/regex extractor | Offline. Labelled `provider_kind: "deterministic keyword extractor (not an LLM)"` in every response, with `provider_is_fake: true`, and note drafts add a `provider_notice`. `AI_FAKE_MODE=demo_disagreement` makes pass 2 disagree on the first measurement, to demonstrate a disputed value. |
| `azure` | `app/ai/azure_provider.py`: Azure OpenAI through Semantic Kernel, using strict JSON-schema structured output | Config-gated. See §7. **Not run live** in this build. |
| `local` | `app/ai/local_provider.py`: a pinned open-weights GGUF model on a loopback-only `llama-server` on this machine | See §2a. **Run live on synthetic cases** (§2a.4). General-purpose model, **not a medical model**. |

Every run, capability answer and note draft carries `provenance: {provider, model, revision, mode, synthetic}`, where `mode` is `local`, `cloud`, `fake` or `none`, and `synthetic: true` only for the fake (a keyword matcher, not a model).

There is **no fallback** between providers. If every pass fails, the request returns `502 AI_ADAPTER_ERROR`.

**Provider contract** (`app/ai/adapter.py`):
- Input is `RedactedPrompt` only, checked at runtime. A `RedactedPrompt` can only be created by `app.privacy.pii.redact_segments`.
- Output is raw JSON text.
- The **server** validates the output against `ExtractionOutput` (`extra="forbid"`, strict).

The provider prompt (`app/ai/prompts.py`, versioned) passes segments as delimited data and says they are not instructions. This does not prevent prompt injection. The real controls are the strict schema, grounding, and the raise-only boundary.

**Because the fake is deterministic, its three passes always agree.** MAKER voting is therefore exercised only through test perturbations and `demo_disagreement` mode. Agreement figures from the fake say nothing about model reliability.

## 2a. Local open-weights provider (`AI_PROVIDER=local`, 2026-10-05)

### 2a.1 What runs

```
backend (FastAPI) ── RedactedPrompt ──► LocalLlamaProvider ── HTTP, Bearer key ──► llama-server on 127.0.0.1:8091
                                         (app/ai/local_provider.py)                  (scripts/start_local_llm.py)
                                                                                      pinned GGUF, SHA-256 checked
```

- Same contract and safety path as every provider (§1–§6): redaction first even though nothing leaves the machine,
  strict schema, grounding, MAKER voting, raise-only urgency, per-field human review. No fallback to the fake or a cloud.
- The JSON schema is sent **with** its constraints (`strict_json_schema(..., keep_constraints=True)`): llama.cpp turns it
  into a sampling grammar, so lengths, item counts and the segment-id pattern hold. Azure still gets the stripped form.
- The local prompt adds one line, "write the JSON on a single line" (`prompt_version …+compact`, stored per run):
  pretty-printed JSON cost indentation tokens on a laptop.
- `GET /ai/capabilities` adds `ready` and `not_ready_reason` (`model_not_installed`, `server_not_started`,
  `server_unreachable`, `server_auth_failed`, `wrong_model_loaded`, `server_error`). An extraction checks the same before
  any case text is processed and answers **503 `LOCAL_MODEL_UNAVAILABLE`**; a pass that times out or returns an
  incomplete/other-model reply abstains; all passes failing is 502 `AI_ADAPTER_ERROR`.

### 2a.2 Model decision (hardware: Apple M5, 10 CPU cores, 8-core GPU/Metal 4, 16 GB unified memory, macOS 27.0.1)

| Model (pinned) | Size | Licence | Result on this machine | Decision |
|:---|--:|:---|:---|:---|
| **Qwen3-4B-Instruct-2507** Q4_K_M (`unsloth/Qwen3-4B-Instruct-2507-GGUF@a06e946b`) | 2.50 GB | Apache-2.0 | passes the gate (§2a.4); server RSS ≈ 5.0 GB with 3 slots × 6144 ctx | **default** |
| Gemma 4 E4B-it Q4_0 (`ggml-org/gemma-4-E4B-it-GGUF@b8093469`), named in architecture §6 | 4.59 GB | Apache-2.0 | loads on llama.cpp b11146; **0/60 passes finished within 90 s** (≈1000 tokens per pass); cause not investigated further | rejected for now |
| Gemma 4 E2B-it Q4_0 (`ggml-org/gemma-4-E2B-it-GGUF@b4243c15`) | 2.84 GB | Apache-2.0 | pinned, not measured | available, unmeasured |

All three are general-purpose instruct models, **not medical models**, and not clinically validated. Runtime: Homebrew
llama.cpp 0.5.0 (build 11146), Metal. Single-stream decoding ≈ 40 tokens/s (Qwen) and ≈ 35 tokens/s (E4B); three parallel
MAKER slots share that (≈ 12 tokens/s each), so three passes cost about three times one pass.

### 2a.3 Server hardening

`scripts/start_local_llm.py` re-checks the file's SHA-256, writes a fresh random API key (0600), then runs llama-server
with `--host 127.0.0.1 --api-key-file … --no-webui --no-slots --offline --reasoning off -cram 0`, never `-v`. Checked live:
listening on 127.0.0.1 only; no key → 401; `/slots` → 501; after the 20-case runs the server log contained none of the
case words. `LOCAL_LLM_URL` must be `http://<loopback>:<port>` (refused otherwise, value not echoed); httpx ignores proxy
variables; every reply must name the pinned alias. See docs/11 addendum.

What is and is not verified: the download script, the start script and the backend at startup each check the file's
SHA-256 against the pin; the backend then trusts the server it talks to. It checks only the alias and the API key, so a
process that bound the port first (a misconfigured second server, or a hostile local process) would receive the key and
redacted text. Run nothing else on `LOCAL_LLM_URL`'s port. `LOCAL_LLM_URL` accepts literal loopback IPs only, not
`localhost`.

### 2a.4 Measurement gate (council, 2026-10-05) and results — a smoke test, not a model evaluation

`scripts/measure_local_llm.py`: 20 synthetic English cases (7 with no red flag), real redaction → provider (3 passes at
0.1/0.2/0.3) → schema → grounding → vote. Gate agreed **before** measuring: grounding pass rate ≥ 70 % and median 3-pass
time ≤ 30 s. Expected values and flags are the author's synthetic labels, not clinical ground truth.

| Run | Median / max | Valid passes | Grounding | Values found | Red flags | Flags not in labels | False raise on a negative case |
|:---|:---|:---|:---|:---|:---|:---|:---|
| Qwen, first try (stripped schema, pretty JSON, 30 s timeout) | 28.4 / 30.0 s | 22/60 | — (12 cases had < 2 valid passes) | 6/21 | 2/15 | 1 | 0/7 |
| **Qwen, constrained schema + compact JSON (shipped)** | **17.7 / 25.8 s** | **60/60** | **77 %** | **19/21** | **0/15** | 0 | **0/7** |
| Qwen + red-flag values listed in the prompt | 24.3 / 66.6 s | 60/60 | 52 % ✗ | 16/21 | 5/15 | 4 (2 arguably correct) | 0/7 |
| Gemma 4 E4B (same settings as the shipped row, 90 s timeout) | 90 / 90 s | 0/60 | — | 0/21 | 0/15 | 0 | 0/7 |

**Reading this honestly:** the shipped configuration finds most measurements and symptoms with verbatim quotes, but it
**does not surface red-flag mentions**: the model is held to the schema grammar but never sees the allowed flag values, so
it leaves the list empty. Listing the values raised recall to 5/15 but broke the grounding gate and doubled the worst
latency, so it is not used. This is not a safety regression — red flags from AI are only candidates, and the rules
engine always asks the red-flag screen (§6) — but the AI adds no red-flag help with this model. 20 synthetic cases are
far too few to estimate accuracy; nothing here is a clinical performance claim.

The gate measures **grounding precision and latency only**. A model that says little scores well on it, and the shipped
model passed with 0/15 red-flag recall. That is acceptable **only because** the rules engine asks the red-flag screen
itself; if that ever changed, recall and false-raise criteria would have to join the gate. The registry records each
model's result (`gate: passed | failed | unmeasured`), `--list` prints it, the start script warns on anything but
`passed`, and capabilities show it as `smoke_gate`.

### 2a.5 Setup

```
# from backend/
../.venv/bin/python scripts/download_local_llm.py --list
../.venv/bin/python scripts/download_local_llm.py qwen3-4b-instruct-2507-q4km     # ~2.5 GB, pinned, SHA-256 checked
../.venv/bin/python scripts/start_local_llm.py                                    # separate terminal; Ctrl-C stops it
# .env: AI_PROVIDER=local (AI_TIMEOUT_S defaults to 60 for local; optionally GUARDRAILS_ENABLED=1, §2b)
../.venv/bin/python scripts/measure_local_llm.py                                  # optional: the gate above
RUN_LIVE_LOCAL_MODEL_TESTS=1 ../.venv/bin/python -m pytest -m live tests/ai/test_local_provider.py
../.venv/bin/python scripts/e2e_local_ai_check.py http://localhost:8101           # HTTP walkthrough (8 checks)
```

Startup order: start the model server first (it re-hashes the file, prints the llama.cpp build — tested with 11146 —
and sends one synthetic warm-up request before printing "ready"), then the backend (it re-hashes the file too, ≈ 1–2 s,
and warms up the guardrails when enabled). If the model server is stopped, the backend keeps running and extraction
answers 503 `LOCAL_MODEL_UNAVAILABLE`; restarting the model server needs no backend restart (the key is re-read).
Run one extraction per case at a time: three passes fill the three server slots, and a second concurrent extraction
queues behind the first.

Memory: keep one model resident and start it before a demo (first load ≈ 2 s once cached; cold disk loads are slower).
The local model, IndicConformer ASR (≈ 2.8 GB) and Chandra OCR all fit on 16 GB only if not all are busy at once.

## 2b. Optional NeMo Guardrails layer (`GUARDRAILS_ENABLED=1`, 2026-10-05)

`app/ai/guardrails.py`, installed with `backend/requirements-guardrails.txt` (nemoguardrails 0.24.1). Off by default.

```
redacted segments ─► INPUT RAIL ─► provider passes ─► schema ─► grounding ─► vote ─► OUTPUT RAIL ─► store (T2) ─► human review
                      │ blocked → 422 GUARDRAIL_BLOCKED, provider never called, nothing stored
                      │ rail error / timeout / disagreement with its detector → 503 GUARDRAIL_UNAVAILABLE, nothing released
```

- **What NeMo does here, honestly.** `LLMRails.check_async` runs one input rail and one output rail, both written as custom
  Python actions that call deterministic detectors in `app/ai/guard.py`:
  - `check_input`: instructions to the model, attempts to change urgency / override the rules / skip review, and requests
    for a diagnosis or a prescription. Ordinary patient speech ("is it serious?", "no chest pain") is not matched.
  - `check_output`: everything the note guard blocks (diagnosis, prescription, instruction, identifier patterns) plus
    urgency-lowering ("not urgent", "can wait", "safe to send home") and review-bypass language, applied to every
    model-produced string (values and candidates; numbers and enum values carry no free text).
- **NeMo adds no detection of its own**: no LLM self-check rail, no dialog rails, no embedding model. It is a standard rail
  framework over our own patterns, which are heuristic and can be evaded. Capabilities say so
  (`guardrails.adds_new_detection: false`). Schema, grounding, MAKER, raise-only urgency and per-field review stay the
  main controls. As a cross-check, a rail result that disagrees with the detector it wraps is treated as a failure (503).
- **Fail closed.** A blocked request stores nothing and returns `{stage, reason}`; the audit records
  `guardrail_<reason>` only. The health worker continues on the manual form.
- **Privacy.** Telemetry is forced off before import (`NEMO_GUARDRAILS_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`); tests check
  the package's own opt-out and that the rails open no network connection. The rails see redacted text only.
- **Trade-off.** Blocking a whole request for one matching sentence loses AI help for that intake (never the intake
  itself). This is the requested fail-closed behaviour. It also over-blocks: a quoted patient question such as "what
  disease do I have?" or a value like "can wait" blocks the whole extraction, although raise-only already makes AI
  "lowering" harmless. With `GUARDRAILS_ENABLED=0` (the default) **none of the new input/output detectors run**:
  instruction-like text is only flagged (`possible_instruction_text`), as before. Making the detectors unconditional is a
  product decision left open (council review, 2026-10-05).
- **Not covered:** image descriptions (they keep their own field-level guard, docs/18 §6) and the template note (already
  guarded). Tests: `tests/ai/test_guardrails.py` (28, real nemoguardrails, no model).

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

`app/ai/grounding.py` is pure. It runs on every model value in the extraction path, **before** voting:

- **Quotes:** the quote must occur in the cited redacted segment (case- and whitespace-insensitive).
- **Numbers:** the value, and `value2` where present, must occur as a number in the quote. Number words are parsed with the voice extractor's parser, so "one hundred and forty over ninety" grounds 140/90. "one forty over ninety" does **not**: that parser deliberately never sums "one forty". The value is dropped with reason `value_not_in_quote`.
- **Text values:** every word of 3 or more characters must occur in the quote.
- **Identifier patterns:** a value that matches one is dropped (`pii_pattern_in_output`).
- **Negation must govern the mention** (fixed after the final council review). A mention counts as negated only if the model says so **and** every quote is a single clause that opens with a negation cue: "No chest pain", "denies chest pain", "Patient denies chest pain".
  - A cue elsewhere is not tied to the mention, so the mention stays present and gets a flag (`negation_conflict` or `negation_unsupported`). For example, in "Severe chest pain since morning, no fever" the "no" belongs to fever.
  - Errors can only lead to an extra review. They never hide an alarm.
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
  - **Alarms first.** Red-flag mentions lead the claims and the summary, so the 500-character cap can never cut them.
  - Every unreviewed sentence in the summary is tagged `[unreviewed]`.
  - Fixed disclaimer, `clinical_use_allowed: false`, `requires_sign_off: true`.
  - Urgency:
    - **Latest case triage run exists:**
      - `recorded_urgency` is the rules engine's result, and the only recorded urgency.
      - `if_ai_suggestion_accepted` is what `enforce_raise_only(result, suggestion)` would give. A downgrade is refused with the engine's reason. A raise is shown as `raise_suggested` and changes nothing until a human acts on it.
      - The suggestion's quotes and source links are in `ai_suggestion_evidence`.
      - The warning `triage_run_predates_extraction` appears when the triage run is older than the extraction.
    - **No triage run:** `"not determined by the rules engine — human triage required"`.
  - **No AI endpoint writes `triage_runs`.** A static test checks this.
  - **Sign-off is not implemented** (Phase 8). `requires_sign_off: true` is a statement, not a workflow. A note is a snapshot and does not update when fields are reviewed later. `draft()` uses the newest triage run of the case.

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

- **Azure not run against a real deployment.** The local provider has run live on 20 synthetic cases (§2a.4); the Azure path is covered by one mocked Semantic Kernel contract test (strict `response_format`, temperature per pass, only redacted text sent).
- **Redaction misses:** 5 known name/DOB misses (lowercase or uncued Indian names). That is why real patient text must not go to a cloud model and Azure requires `AI_CLOUD_SYNTHETIC_DATA_ONLY=1`.
- **Romanised Hinglish:** it passes the Latin-script check, but both redaction and the fake handle it poorly. **Untested.**
- **Redaction removes clinical data:**
  - The full-date (DOB) pattern also redacts onset dates, so "since 28/09/2026" becomes `[DATE_REDACTED]` and cannot be extracted.
  - A run of 8 or more bare digits fails the whole request closed, e.g. "glucose 245 312 280" → `422 PII_DETECTED`. This fail-safe is accepted as is.
- **Spoken numbers:** see §4. "one forty" is dropped, not guessed.
- **One bad segment blocks the whole request.** Fail-closed redaction applies per request: one Hindi or Odia word in otherwise English text (`unsupported_script`), or a run of bare readings, returns 422 for the entire extraction.
- **Silent over-redaction:** "LMP 12/03/2026, G2P1" can become "LMP [DATE_REDACTED], [PERSON_REDACTED]", and romanised "Bukhar hai" can be redacted as a name. The value is lost, and no error is raised.
- **The form-hint path is not guarded by `enforce_raise_only`.** Accepted values come with hints for the triage form. If a person copies a wrong reviewed value into the form, it changes the rules input itself, and nothing records where a triage input came from. Mitigations:
  - hints exist only for human-reviewed values;
  - repeated readings that differ get no hint (`conflicting_readings`);
  - red-flag mentions are only candidates for the screen;
  - the red-flag screen is always asked.
- **Names in corrections:** reviewer corrections go through the full redaction pass (Presidio and patterns) and are rejected if anything is found. This is heuristic and can miss names.
- **Repeated readings use positional keys (`spo2#2`).** A real LLM that orders readings differently from pass to pass will produce disputes.
- **Translator:** a missing torch or IndicTransToolkit surfaces only on the first request, recorded as `translation_failed` with the source skipped.
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
  - NeMo Guardrails with an LLM self-check rail (the optional layer in §2b runs our deterministic detectors only)
  - SNOMED / RxNorm codes in the note
  - a frontend (Phase 8)

## 9. Data, consent and audit

- **Consent:**
  - Extraction needs `ai_assist`, which implies `triage`. Every read, review and note re-checks both.
  - **Roles:** the ANM who created the case, or a medical officer.
- **Retention** (also in docs/04 §5): AI rows are kept after withdrawal, every AI read is refused while `triage` or `ai_assist` is withdrawn (served again only after re-consent), and there is no deletion path — the append-only triggers also block deletion.
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
| `503 LOCAL_MODEL_UNAVAILABLE` | `AI_PROVIDER=local` but the model or its server is not ready (`details.reason`); nothing was sent |
| `422 GUARDRAIL_BLOCKED` | An optional guardrail blocked the input or output (`details.stage`, `details.reason`); nothing stored (§2b) |
| `503 GUARDRAIL_UNAVAILABLE` | The guardrail layer failed, timed out or disagreed with its detector; nothing released (§2b) |
| `502 AI_ADAPTER_ERROR` | Every provider pass failed (no fallback) |
| `422 AI_NO_INPUT` | Nothing to extract |
| `409 IDEMPOTENCY_KEY_REUSED` | The same key was sent with a different request (a request hash is stored, never the text) |
| `422 PII_DETECTED` | Identifier pattern after redaction, or in a correction |
| `422 AI_INPUT_UNSUPPORTED_LANGUAGE` | Text is not English (Latin script) |
| `409 CONSENT_WITHDRAWN` | Consent changed while the request was running |
| `403 CONSENT_REQUIRED` | Consent for this purpose is not in effect |
| `409 REVIEW_CONFLICT` | The field was reviewed by someone else |
| `422 ACCEPT_REQUIRES_VALUE` | A disputed value cannot be accepted |
| `422 CORRECTION_REQUIRED`, `CORRECTION_INVALID`, `CORRECTION_UNEXPECTED` | Correction missing, wrong type, or not allowed for this outcome |
| `409 REVIEWED_AT_SOURCE` | OCR value; change it in the document review |

## 12. Verification (2026-10-03)

- **Backend:** `946 passed, 10 skipped (opt-in live), 5 xfailed`, up from the Phase 5 baseline of 831 passed. The 116 Phase 6 tests in `backend/tests/ai/` cover:
  - grounding, voting, guard and redaction table tests on realistic redacted inputs
  - adapter contract, config refusals, mocked Azure contract
  - consent withdrawal mid-flight (`ai_assist` and `triage`): nothing persisted
  - raise-only: a GREEN suggestion on a rules-RED case → RED, refused; a unanimous RED raise on YELLOW → shown, not recorded; a split vote is withheld
  - `triage_runs` unchanged by AI endpoints, plus the static no-write check
  - adversarial fake: fabricated values dropped, alarms kept
  - P1 pure functions and endpoints; mocked translation
  - **reviewed OCR merge** (`test_ocr_merge.py`), driven through the real OCR attest/review endpoints with replayed engines, not a live OCR engine. It checks:
    - only reviewed values merge, as `human_reviewed`, with document, field, page and region provenance;
    - OCR content never reaches the provider;
    - a merged value can't be re-reviewed (`409 REVIEWED_AT_SOURCE`);
    - no form hints;
    - the note cites the values as printed;
    - a reviewed OCR Hb satisfies the maternal Hb requirement;
    - no `triage_runs` write.
  - **retention after withdrawal:** AI rows are kept, reads are refused while consent is withdrawn, and reads reopen after re-consent (`test_ai_rows_are_kept_after_withdrawal_and_served_again_only_after_reconsent`)
- **HTTP walkthrough:** `backend/scripts/e2e_ai_check.py` passed **18/18** against a live uvicorn server with `AI_PROVIDER=fake AI_FAKE_MODE=demo_disagreement`. Steps: provider label → consent → extraction (redacted, located quotes, red-flag mention) → disputed BP → review → reviewed view → rules triage → note (recorded GREEN; RED raise suggested, source-linked, alarms first; claims cited; sign-off required) → withdrawal → 403 → audit has no values → chain verifies.
- **Fixed by the pre-PR audit:** (1) missing-information rules used OCR keys that the OCR pipeline never emits (`ocr:hb` instead of `ocr:hemoglobin`; glucose keys), so a reviewed OCR Hb was still listed as missing (safe, but wrong); a test now checks every OCR key against the OCR lexicon. (2) Note claims printed the raw OCR value dict; they now show comparator, value (or qualitative result), unit and the report's own printed flag, labelled "printed flag". The printed range stays in the field and is not repeated in the sentence, because a long range such as "1,50,000 - 4,50,000" would trip the identifier guard and block the claim.
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

**Final council review of the implemented diff (2026-10-03).** Same format: 5 advisors, 5 peer reviews, chairman.
- **Confirmed sound:**
  - no AI code writes `triage_runs`;
  - every path that sets urgency goes through `enforce_raise_only`;
  - the T1/T1b/T2 consent re-checks hold;
  - audit records ids, enums and counts only.
- **Found and fixed before the PR:**
  - A negation cue anywhere in a quote could mark a red flag as denied. Every reviewer ranked this finding first. Negation is now scoped to the mention, with 6 regression tests.
  - Alarms could be truncated out of the summary. They now come first.
  - Unreviewed summary sentences read as fact. They are now tagged `[unreviewed]`.
  - The urgency suggestion's evidence was dropped. It is now stored and shown.
  - An idempotency key could be reused with a different body. That now returns 409.
  - Misleading `final` / `raise_applied` names were replaced by `recorded_urgency`, `if_ai_suggestion_accepted` and `raise_suggested`.
  - The guard did not catch hedged diagnoses ("likely dengue"). Now it does.
  - Names in corrections were stored. Corrections now get a full redaction pass.
  - Repeated readings gave conflicting form hints. Hints are now withheld when readings differ.
  - The translator could load twice. Its loader now has a lock.
  - Wording: "independent passes" and "cannot be bypassed" corrected; `synthetic_provider` → `provider_is_fake`. The mandated "AI-drafted, pending review" disclaimer (docs/04) is kept, and a `provider_notice` now says when the values came from the fake.
- **Documented, not fixed (§8):** sign-off, the form-hint path, over-redaction, whole-request fail-closed, positional reading keys.
- **Later:**
  - a synthetic gold set with an extraction scorer;
  - romanised Hinglish synonyms;
  - an adversarial negation fake mode;
  - provenance on triage inputs;
  - purge on withdrawal;
  - Azure data-residency review before any cloud use.
