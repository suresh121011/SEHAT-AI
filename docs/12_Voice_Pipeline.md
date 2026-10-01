# 12 — Voice Pipeline (Phase 4): Decisions, Design, Verification

> **Research prototype, not a clinically validated device.** Voice input is non-diagnostic. It only
> *pre-fills* measurements after a health worker has checked each one. The deterministic rules engine
> (docs/10) still decides urgency, and a named reviewer still signs off. SEHAT AI is "DPDP-ready by
> design" only and does not claim CDSCO clearance or DPDP compliance.

Status labels used throughout:
- **tested-real**: a real engine or model ran in this repository's tests.
- **tested-mock**: contract tests with a mocked provider only.
- **unverified**: built but not exercised against the real engine.
- **unsupported**: the component does not cover this case.

Section §9 records the current status of each one.

---

## 1. Decision record

All sources were accessed on 2026-09-30.

| Component | Architecture doc §9 said | Evidence found | Decision |
|---|---|---|---|
| VAD | Silero VAD | `silero-vad` 6.2.3 is MIT-licensed. It works at 8 or 16 kHz, in 512-sample windows at 16 kHz. The model ships in the package (no download). The pip package imports `torch` even in ONNX mode. [github.com/snakers4/silero-vad](https://github.com/snakers4/silero-vad) | **Used.** Loaded with `load_silero_vad(onnx=True)`. It is a detector only. |
| STT, lightweight | Silero STT | The `snakers4/silero-models` models are CC BY-NC 4.0 (non-commercial). There is no Hindi or Odia STT. [github.com/snakers4/silero-models](https://github.com/snakers4/silero-models) | **Dropped.** The licence and language coverage both rule it out. |
| STT, online | Saaras V4 | Sarvam `saaras:v4` works on `POST https://api.sarvam.ai/speech-to-text` with the header `api-subscription-key`. It supports `en-IN`, `hi-IN` and **`od-IN`**. The REST limit is under 30 s. Modes are transcribe, translate, verbatim, translit and codemix (codemix is v3 only). It returns **no confidence score**. [docs.sarvam.ai STT](https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe) | **Used as the `cloud` engine.** Off by default. It needs `voice_cloud` consent. |
| STT, offline | IndicConformer "30M" | No 30M model exists. `ai4bharat/indic-conformer-600m-multilingual` is MIT-licensed and **gated**. It covers the 22 scheduled languages, including Odia, but **not English**. It is about 2.5 GB, in ONNX plus TorchScript form, and loads with `trust_remote_code`. [HF model card](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) | **Used as the `local` engine.** Revision `e9b71b36…` is pinned. It is downloaded only by an explicit script. |
| Odia native | "Presear Dakshini" | Only a marketing page exists. `dakshini.presear.com` did not resolve. There is no public API, weights, licence or benchmark. | **Not integrated. The question stays open.** Odia is served by Saaras (`od-IN`) and IndicConformer (`or`). |
| Translation | IndicTrans2 | The models are MIT-licensed, with the `ory_Orya` code. `indic-en-dist-200M` exists. | **Deferred to Phase 6.** Hindi and Odia text is never sent to an LLM here, since the privacy gateway fails closed on non-Latin script (docs/11). Original transcripts are kept. |
| TTS | Indic Parler-TTS | It is Apache-2.0 and gated, with 0.9 B parameters. It is slow on CPU. Meta MMS-TTS is CC-BY-NC. | **Sarvam Bulbul v3** is used, off by default and needing `voice_cloud` consent. The fallback is the browser's `speechSynthesis`, only with a voice that matches the language. The visible read-back is always shown. |
| Whisper | — | Whisper has no Odia language code. | Not used. |
| Odia number words 21–99 | — | Unicode CLDR `common/rbnf/or.xml` (Unicode-DFS-2016) spells 0–99 one by one, `100: ଶହେ[ >>]`, `200: << ଶହ[ >>]`, decimal `ଦଶମିକ` (read 2026-10-01). CLDR `hi.xml` adds spellings such as निन्यानबे. | **Added (2026-10-01)** as a closed table, `draft_unreviewed`. CLDR is a written standard: it does not show what an ASR model writes for Odia speech (§9.3). |
| Sarvam data handling | — | Sarvam's privacy policy (last updated 2026-07-29, read 2026-10-01): content kept for an account-set period, **default 30 days after last access**; used for training **unless the account opts out**; personal data **may be processed outside India**. Its product pages claim no retention, no training and India-only processing. | The notice states the privacy policy, the less protective of the two (notice `2026-10-01.1`). The earlier wording "up to 30 days" overstated a limit and omitted processing outside India. |
| arXiv:2605.03073 | "numbers captured 0.027–0.16 of the time" | This is a single-author preprint about **Telugu** entity hit-rate. | Cited as motivation only. The demo script no longer applies the figure to Odia. |

**Final council review of the implementation** (llm-council, 5 advisors, 2026-09-30) found defects that were fixed before hand-over, each with a regression test:
- The extractor produced confirmable wrong values with no flag: साढ़े उनतालीस gave 39.0, "one twenty" gave 21, "not sure if pregnant" gave not pregnant, the "2" in "spo2" became a stray number, and "was 95" was confirmable as current.
- Risk flags did not block one-click confirmation.
- A `pending` row could be stranded by a failure after T1.
- Retry after a failure was a no-op.
- Transcripts stayed readable after withdrawal.
- The Listen button claimed audio played before it had.
- The consent checkbox omitted training and read-back egress.
- Docs and the demo script contradicted each other.

**Pre-implementation council review (llm-council, 5 advisors plus peer review).** The review changed the plan in these ways:
- **Claim corrected.** "Voice cannot lower urgency" was false. The accurate statement is: *voice cannot bypass or alter the rules engine; a wrong value that a reviewer confirms can still under-triage.*
- **Upload format.** Multipart upload was replaced by a raw `audio/wav` body. Starlette 1.7 spools multipart parts over 1 MB to disk (`starlette/formparsers.py`).
- **No `auto` engine.** The engine choice is explicit, with no fallback in either direction.
- **Consent timing.** Consent is checked **before** any cloud upload. The re-check after the call can only discard the result.
- **Who confirms.** Only the case reviewer (the creator ANM or an MO) can confirm. The patient can record.
- **Idempotency.** A `pending` row is written before any engine runs.
- **Consent table.** `consent_events` is rebuilt with foreign keys off, using SQLite's documented procedure.
- **Extractor.** Extractor hardening was added (see §5).
- **Scope.** TTS and the local model were made optional.

## 2. Architecture

```
Browser (VoiceRecorder)
  MediaRecorder → decodeAudioData → OfflineAudioContext(1, n, 16 kHz) → PCM16 WAV   (≤30 s, in tab memory)
  ── raw audio/wav body via the same-origin proxy (session JWT attached server-side) ──►
FastAPI POST /cases/{id}/voice/transcriptions?language=en|hi|or&engine=local|cloud&idempotency_key=<uuid>
  feature gates (VOICE_* flags, engine readiness, language support)          → 404/503/422, no data read
  capped streaming read (413) → validate_wav (16 kHz, mono, PCM16, ≤30 s)     → 400 AUDIO_INVALID
  T1 [txn]  case access (creator) → consent triage (+ voice_cloud for cloud)  → 403 CONSENT_REQUIRED (audited)
            idempotency: same key → stored result | 409 IDEMPOTENCY_CONFLICT | 409 IN_PROGRESS
            else INSERT voice_transcriptions(status='pending')
  (no transaction held)
            Silero VAD → no speech → status no_speech (engine not called)
            engine = local  → IndicConformer (HF_HUB_OFFLINE=1, local_files_only) — never calls the cloud
                   = cloud  → Sarvam Saaras v4 (fixed host, key in header only)  — never falls back
  T2 [txn]  consent unchanged since T1? → finalise row, store candidates, audit voice_transcribed
            else → status failed/consent_changed, audit, 409 CONSENT_WITHDRAWN (result discarded)
  deterministic extractor → candidates (never urgency, never symptoms or red flags)
Reviewer (ANM creator / MO): Confirm | Correct | Not sure | Wrong/not said   → append-only events
GET /cases/{id}/voice/prefill → confirmed/corrected values shaped as TriageInput fragments
Reviewer submits triage through the EXISTING POST /cases/{id}/triage (unchanged, re-validated)
```

The voice package never imports or calls the rules engine, and never submits triage (`app/voice/`).

## 3. Privacy and consent

- **Consent purposes:** `triage`, `ai_assist` and **`voice_cloud`**, with notice version `2026-10-01.1` (earlier consents keep the version they were given under).
  - `voice_cloud` is opt-in. It is effective only while `triage` is granted.
  - A decision that omits it records it as **declined**.
  - Withdrawing `triage` cascades to `voice_cloud` and `ai_assist`, with the method `cascade_from_triage`.
- **Notice wording (en, `2026-10-01.1`):** "If you allow it, your voice is sent over the internet to Sarvam AI, a private company, to turn it into text, and the values heard (for example your temperature) may be sent to Sarvam to be read back aloud. Sarvam's privacy policy (29 July 2026) says it keeps such content for a period the account holder sets (by default 30 days after it was last used), may use it to train its models unless the account has opted out, and may process data outside India. This software does not check whether this system's account has changed those settings." The checkbox states the same facts.
  - The hi and or wording is an unreviewed draft, shown with the draft banner. The material facts each language must state, and the open questions for reviewers, are in [docs/13](13_Language_Review_Checklist.md). A test checks that the facts' key words appear in every language. It does not check meaning.
  - **Whether an opt-out or a shorter retention is in place is not verified by this software.** It is the deploying organisation's responsibility. Sarvam's product pages and its privacy policy disagree (§1); the notice follows the policy.
- **Audio:**
  - Audio is held in process memory for the request only and read with a hard byte cap. It is not written to the database or disk by this code, and not logged.
  - Online speech is never chosen by default: the page defaults to the on-device engine, or to none (when the language has no on-device model) until the reviewer picks "online", and it states where recordings go ("will be sent over the internet to Sarvam AI" / "nothing is sent to Sarvam"). The Listen button says whether it uses the online voice (sends the read-back text to Sarvam) or the device voice. Before 2026-10-01 English with `voice_cloud` granted silently selected the cloud.
  - Consent is re-checked right before audio is sent to Sarvam (after VAD). A withdrawal during VAD now stops the upload; a withdrawal during the provider call still only discards the result (T2).
  - In the browser, leaving the voice page while recording discards the recording (it is never uploaded), and an upload still in flight is aborted. Aborting stops the browser sending; bytes the server or Sarvam already received cannot be recalled. Code-inspected; browser behaviour is in the §9.4 checklist.
  - Out of scope for this claim: the OS or Python allocator, crash dumps, and the browser tab's memory.
  - The Next.js proxy buffers the body in memory (`request.arrayBuffer()`).
- **Transcripts:**
  - Transcripts are stored as untrusted machine output under triage consent. They are rendered as text only, with no HTML.
  - They are kept after withdrawal, as the notice says. Retention and deletion are still deferred (docs/04 §5).
  - Visible to the case creator and MOs, not to supervisors, and only while triage consent is in effect. After withdrawal, reading them is refused (403, audited). The rows are kept, as the notice says, and cannot be edited: deletion or redaction needs the future retention design.
- **Third parties:** audio sent to Sarvam cannot be recalled. The T2 consent re-check only discards the result.
- **Audit:**
  - New actions: `voice_transcription_started`, `voice_transcribed`, `voice_transcription_failed`, `voice_readback_resolved` and `voice_tts_generated`.
  - `voice_transcription_started` is committed **before** any engine runs. A cloud upload is therefore on record even if the request dies afterwards.
  - Details hold ids, categories and counts only. No transcript text, values or audio (tested).
  - Linked ids may still be personal data.
- **AI boundary:** transcripts never reach an LLM in this phase. Any future use must go through `app.privacy.gateway`. The static boundary tests also check that the only non-JSON request body is this `audio/wav` upload, with enum or UUID query parameters.
- **Bystanders:** a live microphone may capture other people's voices. There is no speaker separation.

## 4. Engines and routing

| Request | Condition | Response |
|---|---|---|
| any | `VOICE_ENABLED=0` | 404 `FEATURE_DISABLED` |
| any | wrong Content-Type / too large / not 16 kHz mono PCM16 WAV | 415 / 413 / 400 `AUDIO_INVALID` (before any engine) |
| any | triage consent not in effect | 403 `CONSENT_REQUIRED` (audited) |
| any | VAD finds no speech | 200 `status=no_speech`, engine not called |
| local | flag off / model not installed | 503 `LOCAL_ASR_UNAVAILABLE` — **never** goes to the cloud |
| local | language `en` | 422 `LANGUAGE_UNSUPPORTED` (IndicConformer has no English) |
| cloud | flag off or no key | 503 `CLOUD_STT_UNAVAILABLE` |
| cloud | `voice_cloud` not granted | 403 `CONSENT_REQUIRED` — nothing uploaded |
| cloud | timeout / 429 / 5xx / auth | 504 / 429 / 503 with a fixed `reason`; provider text is discarded |
| any | engine returns blank text | 200 `status=empty_transcript`, no candidates |
| any | consent changed while processing | 409 `CONSENT_WITHDRAWN`, result discarded (cloud: re-checked before upload too) |
| any | row already closed as abandoned by a retry when the run finishes | 409 `TRANSCRIPTION_ABANDONED`, late result dropped (was a 500) |

The provider's reported language is recorded as a warning. It is never used to switch language.

## 5. Critical-value extraction (`app/voice/extract.py`)

- **Fields:**
  - Temperature, SpO2, pulse, respiratory rate, BP (sbp/dbp), age and pregnancy.
  - Symptom duration is display-only.
  - Symptoms and red flags are **not** inferred. The transcript is shown instead.
- **Numbers:**
  - Digits in any script (Odia ୧୦୨ and Devanagari १०२ both map to 102) and English number words are recognised.
  - Hindi and Odia number **words** are parsed from a closed, `draft_unreviewed` table: Hindi 0–100 with सौ (plus CLDR spellings such as चौवालीस, उनासी, -नबे), and Odia 0–99 with ଶହ (16–99 as spelled in Unicode CLDR, added 2026-10-01). Such candidates carry the `number_words` flag.
  - Odia ଶହେ means one hundred and only **starts** a number (ଶହେ ଦୁଇ = 102, ଶହେ କୋଡ଼ିଏ = 120). "ଦୁଇ ଶହେ" is not read as 200: it is blocked.
  - This was added because the real local model writes numbers as words ("तापमान एक सौ दो डिग्री", see §9.2), which the pre-agreed contingency covered.
  - Words outside the table (other spellings, joined or inflected forms such as ଦୁଇଶହେ, ଶହେରୁ) are never guessed. Those values stay missing, which means human review.
  - **Split decimals.** A decimal word or "." after a parsed number that did not join it ("39 point 5", "बुखार 39 दशमलव 5", "39. 5", "39 and half") blocks the value (`number_modifier_unparsed`); before 2026-10-01 the integer alone (39.0 °C, just below ATP ">39 °C") was confirmable. Read-back quotes the words heard.
  - **Clean-context gate (allowlist, 2026-10-01).** One-click confirmation is offered only for the plain shape "field word, optional filler (is, rate, है, का, ଅଛି …), number, unit, optional filler (है, ଅଛି …)" or "number unit field-word" (102 डिग्री बुखार). Any other word next to the value, a suffix or hyphen glued to the number (୯୦ରୁ, 110-120), or no field word at all gives the blocking flag `context_unclear`. This replaced adding bad words one by one after the final council found 14 realistic phrasings that still gave confirmable wrong values (comparators "below 90" / "90 से कम", ranges, partial counts "15 in 30 seconds", "ऑक्सीजन लगा दो", "50% of her food", a child's age, …). BP next to a stray number ("one sixty over one ten") is `number_sequence_ambiguous`; both BP numbers under 30 ("13 by 9") is `bp_shorthand_possible`. More "earlier" words: परसों, पिछले, ago, last, ଗତ, ପରଶୁ.
  - **Homographs.** Some number words have an everyday second meaning: ବାର is 12 but also "time(s)" (ଅନେକ ବାର, "many times"), and ଏକ / एक / "one" is also "a" (ନାଡ଼ି ଏକ ଟିକେ ବେଶୀ, "pulse a little high"). Before 2026-10-01 these produced confirmable values (respiratory rate 12, pulse 1). Now a value containing ବାର, a lone "one" word, or a number followed by a count word (बार, ବାର, ଥର, दफा, "times") carries the blocking flag `number_word_homograph`. The reviewer must enter the value. A genuine "ବାର" (12) or age "one year" therefore needs typing; that is the intended trade-off.
- **Temperature:**
  - °F is converted to °C exactly, and **never rounded**. Rounding could move a value across ATP ">39 °C".
  - Tested: 102.2 °F converts to exactly 39.0 °C (not above 39), and 102.3 °F is above 39.
  - The UI shows 2 decimals and labels the value "exact value used".
  - A spoken value with no unit is inferred only within the engine's own domain: 25–45 is read as °C, 77–113 as °F.
  - Any other value is `unit_unknown`. It must be corrected and cannot be confirmed.
- **Flags** (each is tested):
  - `unit_inferred`, `unit_unknown`, `out_of_domain_range` (the existing `Vitals` bounds only), `non_integer`
  - `decimal_ambiguity`, for example 100.2 or 1002
  - `negation`: looks both before and after the value within the clause, because Hindi and Odia negators follow the verb
  - `uncertainty`, `temporal_reference` ("yesterday / kal / ଗତକାଲି"), `multiple_values`
  - `oxygen_context`: `on_supplemental_oxygen` is never inferred
  - `bp_order_invalid`, `age_unit_months` (never pre-filled as years), `needs_assignment`
  - `number_word_homograph`, `context_unclear`, `bp_shorthand_possible` (see Numbers)
  - `oxygen_context` is now **blocking** (2026-10-01): confirming only the number would drop the oxygen support, and a reading on oxygen read as room air can under-triage
- **Offsets:** candidates keep **character offsets** into the raw transcript, which the reviewer sees highlighted. No engine gives reliable per-word audio timing and no audio is kept, so the evidence link is the transcript span plus the clip's VAD speech span.
- **Lexicon:** the Hindi and Odia keyword lists are `draft_unreviewed`.

## 6. Read-back policy (`app/voice/readback.py`)

- **Who decides.** Every candidate needs an explicit decision by the case reviewer: the creator ANM or an MO. The patient can record and listen but cannot confirm.
  - **Yes, that's right** (`confirmed`): allowed only when the value was normalised **and carries no blocking flag**. The blocking flags are negation, uncertainty, earlier-time reference, several values, an unparsed number modifier (साढ़े / सवा / "and a half"), a number spoken in parts ("one twenty"), a number word that may mean "times" or "a", unclear context around the value, oxygen support mentioned, possible BP shorthand, unknown unit, out of range, reversed BP, age in months, and needs-assignment.
  - **Change value** (`corrected`): the reviewer enters the value, with no default field or unit. It is validated against the engine domain and stored as `voice_manual_correction`.
  - **Not sure** (`unsure`): the field stays **missing**, which means human review.
  - **Wrong / not said** (`rejected`): the field also stays **missing**.
- **Nothing counts as implicit confirmation.** Silence, a timeout, a closed page, or a failed or unavailable spoken read-back never confirms a value.
- **Prefill.**
  - Only confirmed or corrected values are pre-filled.
  - Different values for the same field become `conflicts`, with no value chosen.
  - Undecided and "not sure" values are listed under `unresolved`, so the reviewer sees what is still open.
  - Prefill is read in one consistent snapshot and **never submits triage**.
  - There is no triage entry form in the frontend yet (Phase 7). The voice page lists the confirmed values with their source, and the ANM enters them in triage; the API is shaped for a future form.
- **Stale decisions.** A decision names the resolution it replaces (`supersedes`). A decision made on an out-of-date screen is refused with 409 `STALE_DECISION`, never silently applied.
- **Spoken read-back.** It uses Sarvam Bulbul if it is enabled and `voice_cloud` is granted. Otherwise it uses a browser voice matching the language. Otherwise the page says "Spoken read-back is unavailable". The text is built server-side, so the endpoint cannot be used as a general TTS relay.
- **Limitation.** Read-back reduces mis-transcription. It does not verify that the measurement itself was right.
- **What one-click confirm means (final council, 2026-10-01).** The extractor proposes candidate vitals from a transcript; it never sets or lowers urgency. One-click confirm is a convenience, not a correctness check: it is offered only when the value matches a narrow allowlist of simple phrasings, and anything else (comparators, ranges, partial counts, past readings, implausible BP, oxygen context, unparsed modifiers) is blocked and must be typed by the reviewer. These gates are pattern lists and are known to be incomplete; they cannot detect speech-recognition errors (e.g. "ninety" heard as "nineteen"), and they have been checked only against a small set of synthetic and test sentences, with Odia coverage weakest. A named reviewer must check every value against the read-back before confirming.
- **UI (2026-10-01).** Decision buttons are disabled while the read-back is playing. The correction form has no default value for any field, including pregnancy. Adding a synthetic sample clip asks for confirmation, because it is stored on the open case.

## 7. API

| Method | Path (prefix `/api/v1`) | Roles | Notes |
|---|---|---|---|
| GET | `/voice/capabilities` | any | enabled/ready per component, language matrix, verification status; no secrets |
| POST | `/cases/{id}/voice/transcriptions?language&engine&idempotency_key` | patient, anm (case creator) | body: raw `audio/wav`, 16 kHz mono PCM16, ≤30 s |
| GET | `/cases/{id}/voice/transcriptions[/{tid}]` | case creator, MO | transcript (untrusted), candidates, resolutions |
| POST | `/cases/{id}/voice/candidates/{cid}/readback` | anm (creator), MO | `{outcome, field?, value?, value2?, unit?}`, no free text |
| POST | `/cases/{id}/voice/candidates/{cid}/tts` | case creator, MO | `audio/wav`, or 503 `TTS_UNAVAILABLE` |
| GET | `/cases/{id}/voice/prefill` | anm (creator), MO | `{vitals, fields, values{sources}, conflicts, note}` |

Example:

```bash
curl -X POST "$API/cases/$CASE/voice/transcriptions?language=hi&engine=local&idempotency_key=$(uuidgen)" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/wav" --data-binary @clip.wav
```

## 8. Configuration

Everything is off by default. A disabled component is never imported, loaded or called, and the app imports without loading torch (checked).

| Variable | Default | Purpose |
|---|---|---|
| `VOICE_ENABLED` | 0 | master switch |
| `VOICE_VAD_ENABLED` | 1 | Silero VAD (needs `requirements-voice.txt`) |
| `VOICE_LOCAL_ASR_ENABLED` | 0 | IndicConformer on this machine |
| `VOICE_LOCAL_MODEL_DIR` | `./models/voice` | git-ignored model directory |
| `VOICE_CLOUD_STT_ENABLED` | 0 | Sarvam Saaras v4 |
| `VOICE_TTS_ENABLED` | 0 | Sarvam Bulbul v3 spoken read-back |
| `SARVAM_API_KEY` | — | never logged; excluded from `Settings` repr |
| `SARVAM_BASE_URL` | `https://api.sarvam.ai` | must be `https://` + a host in `SARVAM_ALLOWED_HOSTS` (code, `app/config.py`: only `api.sarvam.ai`); no path, port other than 443, credentials, query or fragment. Anything else **refuses to start**; normalised to `https://api.sarvam.ai`. |
| `SARVAM_TIMEOUT_S` | 20 | provider timeout |
| `VOICE_MAX_SECONDS` | 30 | clip cap (≤30; Sarvam REST limit) |

**Install and enable.** The venv has no pip.

```bash
uv pip install --python .venv/bin/python -r backend/requirements-voice.txt   # torch, silero-vad, onnxruntime, transformers
# local model (gated, ~2.5 GB, one time): accept terms on the HF model page, then
.venv/bin/hf auth login
cd backend && ../.venv/bin/python scripts/download_voice_models.py
# run
VOICE_ENABLED=1 VOICE_LOCAL_ASR_ENABLED=1 ../.venv/bin/uvicorn app.main:app --reload
```

**Check it is really active.** Call `GET /api/v1/voice/capabilities`. It shows `ready: true` only when the engine is enabled **and** installed or configured. Each transcription records the `engine` and `model_id` that actually ran.

## 9. Verification status

Status as of 2026-10-01 (gap-closure pass). Re-run the commands in §8 and the opt-in tests in §9.2 to refresh it.

Commands run on 2026-10-01, from `backend/` unless stated:

| Check | Command | Result |
|---|---|---|
| Backend suite, before changes | `../.venv/bin/python -m pytest -q` | 530 passed, 9 skipped (opt-in live), 5 xfailed (known PII gaps, docs/11) |
| Backend suite, after changes | same | 568 passed, 9 skipped, 5 xfailed |
| Backend suite, after the final council fixes (gate, split decimals, oxygen, consent before upload, abandoned 409) | same | 607 passed, 9 skipped, 5 xfailed; frontend lint, `tsc` and build pass |
| Local engine, network sockets blocked | `SEHAT_LIVE_LOCAL=1 ../.venv/bin/python -m pytest -m live -k local -v` | hi PASS; English-rejection PASS; or SKIPPED (no Odia fixture) |
| Sarvam reachability | `curl --max-time 15 https://api.sarvam.ai/`, inside and outside the sandbox | **BLOCKED**: DNS resolves (4.247.234.152), TCP connect times out after 15 s; huggingface.co answers. Key configured (presence checked, never printed). Live cloud tests not run. |
| Sarvam reachability, later the same day (3rd attempt) | `nc -vz api.sarvam.ai 443`; `curl -4 -v https://api.sarvam.ai/`; Python `httpx` | **BLOCKED**: TCP now connects, but the TLS handshake never completes (`SSL connection timeout`; httpx `ConnectTimeout` at 12 s). `docs.sarvam.ai` answers 200. The user reports a training opt-out set in the Sarvam dashboard (not verifiable by this software). |
| Live cloud tests | `SEHAT_LIVE_SARVAM=1 SARVAM_TIMEOUT_S=5 ../.venv/bin/python -m pytest -m live -k "cloud or bulbul"` | Fail with `cloud_timeout` (STT) and `tts_unreachable` (TTS): the network, not the code. An uncommitted change in the working tree (a configurable `SARVAM_BASE_URL`, not made in this pass) added a required `base_url` argument to both adapters; the live tests had not been updated and would have failed with a `TypeError`. They now pass `settings.sarvam_base_url`. Odia STT skipped (no fixture). |
| HTTP walkthrough (Gap 3) | scripted client against `uvicorn` with `VOICE_ENABLED=1 VOICE_LOCAL_ASR_ENABLED=1 VOICE_CLOUD_STT_ENABLED=1 VOICE_TTS_ENABLED=1`, synthetic sample clips | **27/27 PASS**. Notice `2026-10-01.1` in en/hi/or; local hi upload `completed` (labelled "local inference (no provider call)"); a साढ़े value refused one-click confirm (409 `CORRECTION_REQUIRED`), then corrected; prefill shows the temp conflict and the "not sure" pulse as unresolved, no value chosen. Cloud **with** consent: a real attempt, stored as `failed / cloud_timeout` (504 after 20 s), no fallback. TTS: 503 `TTS_UNAVAILABLE`. After `voice_cloud` withdrawal: cloud 403 `CONSENT_REQUIRED` in 0.01 s (before upload), TTS 403, local still 200. Audit trail has no transcript words or values; `POST /audit/verify` → `ok: true`. |
| Frontend | `npm run lint`, `npx tsc --noEmit`, `npm run build` (in `frontend/`) | all pass before and after (no frontend test framework exists) |

### 9.1 Results

| Path | Status | Evidence |
|---|---|---|
| Silero VAD (en, hi synthetic speech; silence; noise; clipped) | **tested-real** | `tests/voice/test_audio_vad.py` |
| Extractor (°F boundary pairs, negation, scripts, homographs, Odia CLDR words in sentences) | **tested-real** (pure code; the number tables are tested against written spellings, not speech) | `tests/voice/test_extract.py` |
| Cloud STT contract, consent, idempotency, failures, audit | **tested-mock** | `tests/voice/test_api.py` |
| Cloud STT against real Sarvam (en/hi/or) | **BLOCKED** (not verified): key configured, but `api.sarvam.ai` is unreachable from this network (TCP timeout, re-checked 2026-10-01 inside and outside the sandbox) | `SEHAT_LIVE_SARVAM=1 ../.venv/bin/python -m pytest -m live -k cloud -v` once the host is reachable |
| Local IndicConformer, Hindi, network blocked | **tested-real** (synthetic clip) | `tests/voice/test_live.py` (§9.2) |
| Odia synthetic fixture (`or_fever_102.wav`) | **not generated**: `tests/fixtures/voice/make_odia_fixture.py` (Sarvam Bulbul v3, `od-IN`) is ready but Sarvam is unreachable. When generated, cloud STT results on it are `circular_if_cloud`; the live tests then print CER and numeric hits per clip. | `SEHAT_LIVE_SARVAM=1 ../.venv/bin/python tests/fixtures/voice/make_odia_fixture.py`, then the `local`/`cloud` live tests |
| Local IndicConformer, Odia | **not verified on real Odia speech.** 2 live-microphone clips (one non-native speaker, numbers only, §9.3) found parser bugs but are not evidence of accuracy. No Odia fixture. The 21–99 number words follow CLDR spellings and have never been seen in model output. | needs consented, scripted clips from fluent speakers |
| Local English | **unsupported** | model has no English |
| Bulbul TTS | **tested-mock** only; **BLOCKED** for a real run (same reason as cloud STT) | |
| Frontend path via Next.js (session cookie → same-origin proxy → raw `audio/wav` upload → real local model → read-back → prefill) | **tested-real** over HTTP (2026-09-30) | scripted HTTP client against `npm run dev` + uvicorn |
| Browser rendering, microphone capture, WAV conversion in the page | **tested-real (manual), Chrome desktop only**: §9.4 run 2026-10-01, B1–B12 PASS (including the recorder fixes B3/B4; B10 in a separate session); no Odia browser voice; Safari not run. Not automated. | no frontend test framework or browser automation in this environment |
| Mobile browsers (Chrome Android, Safari iOS) | **BLOCKED**: not run. The microphone needs HTTPS off localhost, and no HTTPS/tunnel setup exists for the dev server. | §9.4 |

### 9.2 Real-engine runs

Run 2026-09-30, Apple M5, 16 GB, Python 3.11, onnxruntime 1.20.1 (CPU), torch 2.14.0.

| Engine | Language | Fixture (synthetic) | Result | Latency | Memory |
|---|---|---|---|---|---|
| local IndicConformer-600M (CTC), network sockets blocked | hi | `hi_fever_102.wav` (3.9 s, macOS voice Lekha) | transcript "मुझे तीन दिन से बुखार है तापमान एक सौ दो डिग्री है"; candidates: duration 3 days, temp 102 °F → 38.89 °C (`unit_inferred`, `number_words`) | model load 2.59 s once; warm inference 0.25–0.38 s | peak RSS ≈ 2.8 GB |
| local, network sockets blocked | hi | `hi_fever_saadhe.wav` (safety case) | transcript "तापमान साढ़े उनतालीस डिग्री है और नब्स एक सौ बीस है"; temp flagged `number_modifier_unparsed`, **not confirmable** (must be entered: 39.5 °C); pulse 120 | warm 0.32 s | ≈ 2.7 GB |
| local | en | — | unsupported (model has no English): explicit 422 | — | — |
| local | or | — | see §9.3 (live microphone) | — | — |
| cloud Saaras v4 / Bulbul v3 | en, hi, or | `en_fever_102.wav`, `hi_fever_102.wav` | **attempted 2026-10-01, not reached**: key configured; every call ended `cloud_timeout` / `tts_unreachable`. DNS resolves `api.sarvam.ai`, but a TCP/HTTPS connection times out from this network, while huggingface.co answers. The explicit-failure paths behaved as designed. Transcription quality is still unverified. | — | — |

The runs are deterministic: the same text came back on repeated runs. Hindi and English Silero VAD found speech in every synthetic clip. This is one synthetic clip per language on one machine; it is not a WER or CER benchmark and not clinical evidence.

**Model loading (security review).** The vendor's `IndicASRModel.from_pretrained` ignores `local_files_only` and `revision` and calls `snapshot_download`. SEHAT therefore does not call it. It imports the reviewed `model_onnx.py` from the local directory and constructs the model from local files. It first checks `model_onnx.py`, `config.json` and the TorchScript `preprocessor.ts` against the SHA-256 manifest written at download time.

The opt-in tests record engine, language, fixture, status, latency and peak memory. Fixtures are synthetic TTS speech, **not a clinical benchmark**. An Odia clip generated by Sarvam TTS and transcribed by Sarvam STT is circular and labelled as such.

### 9.3 Live microphone session (2026-10-01, on-device engine, browser → Next.js → backend)

The developer recorded 10 clips in the voice page: 8 selected as Hindi and 2 as Odia. Every request completed (HTTP 200) with no errors logged. What the real model and the extractor did:

| Spoken (as transcribed) | Outcome |
|---|---|
| "तापमान साढ़े उनतालीस डिग्री … नब्स एक सौ बीस" | temperature blocked (`number_modifier_unparsed`, quoted back); pulse 120 offered |
| "दो सौ दो डिग्री" ×3; "तीन सौ दो" ×3; "एक एक" | out of range, repeated or unlabelled: all blocked |
| "ଦୁଇଶହ ଦୁଇ" ×3 (Odia, joined form) | **bug found**: parsed as 2. **Fixed**: joined hundreds parse as 202, and unknown hundred-words block |
| "एक सौ आठ डिग्रलियस" (garbled unit word) | **bug found**: °F was silently inferred. **Fixed**: `unit_unclear` blocks confirmation |
| "े" (noise only) | **bug found**: status `completed`. **Fixed**: `empty_transcript` |
| English sentences spoken with Hindi/Odia selected | written phonetically in Devanagari/Odia; no values extracted (safe); the UI now asks users to speak the selected language |

Each fix has a regression test built from the real transcript (`tests/voice/test_extract.py`, "live on-device recordings"). This is one speaker in a quiet room, a handful of clips, and Odia numbers only. It is **not** an accuracy measurement.

### 9.4 Browser and microphone checklist (manual)

Fixes made on 2026-10-01 after reading `frontend/src/components/VoiceRecorder.tsx` and the voice page:
- The microphone stayed on if `MediaRecorder` or `AudioContext` failed to start after permission was given. It is now released.
- Leaving the page while recording stopped the tracks, which ended the recorder, whose `onstop` then **uploaded the audio after the user had left**. The recording is now discarded on unmount. An upload in flight is aborted.
- Other fixes: an empty recording gets its own message, sample-clip load failures are shown, and a 429 from the online service keeps the Retry button. Online failures are explained in plain words, and the page says the engine was not switched. The Listen button no longer calls the reviewer-only TTS endpoint for a patient.

Run on `http://localhost:3000` (demo accounts, synthetic speech only).

**Run 2026-10-01** by the developer: Chrome desktop on macOS, local engine, results as reported (browser version not recorded). Safari not run. The backend log of that session shows 12 transcription uploads (8 hi, 4 or), all HTTP 200, no server errors, and no request to `/triage`. Record PASS / FAIL / NOT RUN with the date and browser version. Rows not run stay NOT RUN.

| # | Check | Chrome (desktop) | Safari (macOS) | Chrome Android / Safari iOS |
|---|---|---|---|---|
| B1 | Deny microphone permission → message shown, nothing uploaded | PASS | NOT RUN | BLOCKED (needs HTTPS) |
| B2 | Record 3 s, Stop → mic indicator turns off; transcript appears | PASS | NOT RUN | BLOCKED |
| B3 | Start recording, then navigate away (header link) → mic indicator off; **no POST** to `/voice/transcriptions` in the Network tab | PASS | NOT RUN | BLOCKED |
| B4 | Upload a clip, navigate away before it returns → request shows "(canceled)" | PASS | NOT RUN | BLOCKED |
| B5 | Record silence → `no_speech` shown, no candidates | PASS | NOT RUN | BLOCKED |
| B6 | Record until the 30 s cap → recording stops on its own, upload accepted | PASS | NOT RUN | BLOCKED |
| B7 | Upload's WAV in the Network tab: `Content-Type: audio/wav`, header says 16 kHz mono 16-bit (size ≈ 32 kB per second + 44 bytes) | PASS | NOT RUN | BLOCKED |
| B8 | Stop the backend, record → error shown and Retry appears; start the backend, Retry → transcript (not stuck "busy") | PASS | NOT RUN | BLOCKED |
| B9 | Candidate cards: highlight, flags, read-back text; Listen → device voice or "unavailable" message | PASS | NOT RUN | BLOCKED |
| B10 | Confirm / Change value / Not sure / Wrong on four cards → prefill lists only confirmed and corrected values; the others appear as unresolved | PASS (developer-reported; run in a separate session whose server log was not kept) | NOT RUN | BLOCKED |
| B11 | Say "ଶ୍ୱାସ ଅନେକ ବାର" or "सांस एक बार" (Odia/Hindi selected) → flag "can also mean 'times' or 'a'", Confirm disabled | PASS | NOT RUN | BLOCKED |
| B12 | Nothing submits triage automatically (no POST to `/triage`) | PASS (log: no POST to `/triage`) | NOT RUN | BLOCKED |
| B13 | Does this device have an Odia browser voice? (consent read-aloud, demo Beat 1) | **No Odia voice** (Chrome, macOS): read-aloud shows "unavailable"; the presenter reads the notice | NOT RUN | BLOCKED |

## 10. Known limitations

- No calibrated transcript confidence (Sarvam gives none; IndicConformer's is not exposed). Confidence never affects urgency.
- Hindi/Odia number words are parsed only from a closed draft table. Odia 21–99 follows CLDR's written spellings; spoken or ASR spellings that differ are not guessed. A number spoken in parts ("ଅଶୀ ପାଞ୍ଚ") is flagged and must be entered by the reviewer. Homograph blocking (ବାର, ଏକ/एक/"one", count words) covers only the words listed; other everyday double meanings may exist (docs/13). Hindi/Odia keyword lists, read-back wording and consent translations are unreviewed drafts (docs/13). Hindi/Odia read-back can contain the English fragment "(unit unclear)".
- A value heard correctly but measured wrongly cannot be detected: read-back reduces mis-transcription only.
- Local inference cannot be interrupted once started. Waiting requests give up after 30 s with `local_busy`. A `pending` row older than 180 s is treated as abandoned.
- Temperature measurement site is not captured, and neither is who is speaking (a caregiver versus the patient).
- No per-user rate or cost limit on cloud calls. A client disconnect cannot cancel a cloud call already sent.
- `trust_remote_code` executes code from the model repository. The revision is pinned, the download script writes a SHA-256 manifest, and the code must be reviewed before enabling.
- Local model memory and latency were measured on one machine only (Apple M5, 16 GB): about 2.8 GB RAM, so it is unlikely to fit budget Android tablets. Server or laptop deployment is assumed.
- Retention and deletion of transcripts remain deferred, as for all case data.
- Sarvam's stated data handling conflicts between its product pages and its privacy policy (§1). The account's actual settings are unverified.
- Known gaps recorded by the final council (2026-10-01), not fixed: read-back decisions are not tied to a consent "epoch" (after withdraw and re-grant, earlier confirmations still prefill); the `voice_tts_generated` audit event has no candidate id; a retry after a backend 5xx uses a new key and can send the same audio to Sarvam again. The Sarvam endpoint is configurable (`SARVAM_BASE_URL`) but limited to an HTTPS allowlist held in code (§8), so the "fixed host" in §2 holds: the key, audio and read-back text can only go to `api.sarvam.ai`. Redirects are not followed.

## 11. Deferred (not Phase 4)

- **Phase 6:** IndicTrans2 translation of transcripts.
- **Phase 7:** wiring the prefill response into a triage entry form (no form exists yet; nothing is auto-submitted).
- **Needs people or access:** live Sarvam STT/TTS (network), real Odia speech clips from fluent speakers, native-speaker and clinical review of docs/13, mobile-browser runs (HTTPS), a decision on whether cloud speech should be offered in hi/or before the translations are reviewed.
- **Production hardening:** per-user/per-case cloud request limits and cost caps, cancelling provider calls on client disconnect, transcript retention and deletion, resource limits for local inference, a full `trust_remote_code` security audit (§9.2 records the checks done), Presear Dakshini (no verifiable release).
