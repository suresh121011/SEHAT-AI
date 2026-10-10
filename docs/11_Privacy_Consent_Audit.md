# SEHAT AI — Consent, PII Redaction & Audit (Phase 3)

> **Demo-only. Not for real patients.** Authentication is shared, password-less demo accounts. The backend
> refuses to start unless `ENVIRONMENT` is `development` or `test`. Every intake page shows a demo banner.
> Real-world use stays blocked until real authentication, account separation and authorization are built
> and reviewed.
>
> **Research prototype, not a clinically validated device.** Clinical validation is incomplete, legal
> compliance has not been independently certified, and real-patient deployment is not approved.
> "DPDP-ready by design" is a design goal, not a certification.

---

## 1. What is implemented

| Control | Implementation | Where |
|:---|:---|:---|
| Case records | Server-generated `case_id` and a random opaque `patient_token`. No name or ID number is collected. These are **pseudonymous references, not anonymous data**. | `app/consent.py`, `POST /cases` |
| Purpose-specific consent | Purposes `triage` and `ai_assist`, kept as an append-only history (`consent_events`). The effective state is the latest event per purpose. `ai_assist` only counts while `triage` does. | `app/consent.py` |
| Backend enforcement | Checked inside the protected write transaction. Denials return 403 `CONSENT_REQUIRED` and are audited. | `app/case_triage.py`, `app/privacy/gateway.py` |
| Withdrawal | Purpose-specific. Withdrawing `triage` also records an explicit `ai_assist` withdrawal (`cascade_from_triage`); withdrawing `ai_assist` leaves `triage` in place. | `POST /cases/{id}/consent/withdraw` |
| Notice | Versioned and served by the backend in **en** (`project_draft`) and **hi/or** (`draft_unreviewed_translation`). The version and review status are stored with each consent event. | `app/consent_notice.py` |
| PII redaction | Presidio analyzer, `en_core_web_sm`, and heuristic India recognizers. It fails closed. **This is risk reduction, not anonymization.** | `app/privacy/pii.py` |
| LLM boundary | One gateway. Adapters accept only `RedactedText`. Consent is re-checked before output is returned. **There is no live LLM in Phase 3.** | `app/privacy/gateway.py`, `adapters.py` |
| Audit log | Application-level, append-only, hash-chained, **tamper-evident (not immutable)**. Only supervisors can read it. | `app/audit.py`, `GET /audit/{case_id}`, `POST /audit/verify` |
| Error hygiene | Outermost `SafeErrorMiddleware`, fixed validation messages, request IDs accepted only as UUIDs, `hide_input_in_errors`. | `app/errors.py`, `app/main.py` |
| Migrations | `PRAGMA user_version` steps, each atomic, additive only (legacy tables kept). | `app/database.py` |
| Frontend | Start-case page and consent page: language selector, draft banner, read-aloud that only uses a voice matching the selected language, patient and ANM variants, purpose-specific withdrawal. | `frontend/src/app/intake/` |

**Not implemented (deferred):**
- emergency processing without consent (DPDP s.7(f));
- data deletion/erasure;
- browser speech recognition;
- multilingual AI processing (Phase 6 adds a local IndicTrans2 path, flag-gated, not run against the real model; docs/16 §8);
- a live LLM adapter (Phase 6 adds a structured provider contract, a fake provider and an env-gated Azure provider that has not been run; docs/16);
- a complete non-diagnostic output filter (Phase 6 adds a heuristic guard on note text, `app/ai/guard.py`);
- purge of stored AI output after consent withdrawal (Phase 6 stops serving it, but the rows are kept);
- external audit checkpoints;
- retention enforcement;
- real authentication.

## 2. Data flow

```
Browser (httpOnly JWT) → /api/backend proxy → SafeErrorMiddleware → FastAPI routes
  POST /cases ─────────────────────────► cases (created_by = principal)            + audit
  POST /cases/{id}/consent[/withdraw] ─► [txn] case access → consent_events         + audit
  POST /cases/{id}/triage ─────────────► [txn] case access → scenario → consent(triage)
                                          → app.rules.evaluate_triage (pure) → triage_runs + audit
  POST /triage/process ────────────────► stateless calculator (unchanged; no storage, no identity)
  POST /cases/{id}/ai/extractions (Phase 6, docs/16) ► privacy.gateway.submit_structured: the same T1/T1b/T2 flow,
        each segment redacted independently (any failure blocks the request); persistence runs inside T2.
  (single-string path, unchanged) ─────► privacy.gateway.submit_for_ai_assist:
        T1  [txn] case access → consent(ai_assist) → authz_seq
            normalize → script policy → Presidio → merge/replace → residual sweep   (thread, no DB lock)
        T1b [txn] authz_seq unchanged? → audit pii_redacted(total)                  else 409
            adapter.complete(RedactedText)                                          (cannot be cancelled)
        T2  [txn] authz_seq unchanged? → audit ai_output_returned                   else discard, 409
```

`app/rules` imports no database, consent, privacy or LLM code; a static test enforces this.

## 3. Consent model

| Decision | Events written |
|:---|:---|
| Grant (AI box unticked) | `triage=granted`, `ai_assist=declined` |
| Grant (AI box ticked) | `triage=granted`, `ai_assist=granted` |
| Decline | `triage=declined`, `ai_assist=declined` |
| Withdraw `ai_assist` | `ai_assist=withdrawn` (triage unchanged) |
| Withdraw `triage` | `triage=withdrawn`, plus `ai_assist=withdrawn` (`cascade_from_triage`) if AI was granted |
| Withdraw something not granted | 409 `NOTHING_TO_WITHDRAW` |
| Stale notice version | 409 `NOTICE_VERSION_STALE`; nothing is recorded |

- **Method:** set by the server from the account's role, never by the client.
  - `patient_button` means a patient account pressed "I agree".
  - `staff_attested_verbal` means the **ANM account attests** that the notice was read and the patient agreed. It does not prove the patient's identity or their exact words.
- **No audio or transcript** is collected or stored for consent.
- **Browser speech recognition is not used.** Chrome's `SpeechRecognition` sends audio to a server. Phase 4 speech-to-text is server-side and explicit instead: on-device IndicConformer, or Sarvam AI only with the separate `voice_cloud` consent (docs/12 §3).

**Who can do what** (checked independently of consent):
- **Record or withdraw consent:** the account that created the case.
- **Case triage:** the ANM who created the case, or any medical officer.
- **Read:** the creator, the ANM who took over a patient-started case (§3a), a medical officer or a supervisor.
- **Everyone else, and unknown case IDs,** get an identical 404, and no audit row is written for them.
- **Limitation:** demo accounts are shared (every `patient_demo` user has the same `user_id`), so these rules demonstrate the authorization *code*, not separation between real people.

### 3a. Patient-to-health-worker handover (2026-10-10)

A patient account may start a case, give consent, record voice, mark the body map and upload text documents (docs/14 §3; review of those documents stays with staff). Follow-up questions, document review and the triage form stay with staff, so the patient's account never confirms values that reach triage. To finish the case, an ANM takes it over:

- **`POST /cases/handover {patient_token}`**, ANM only. The case must have been created by a patient account (`cases.created_by_role`, backfilled for older cases from their `case_created` audit row), be inside the ANM's facility scope, and have triage consent in effect (else 403 and a `consent_denied` audit row). Unknown codes, out-of-scope cases and staff-created cases get the same 404 with no audit row. A case is taken over once (`cases.handled_by`); the same ANM may repeat the call; another ANM gets 409. Audit: `case_handed_over` (scenario and facility code; never the case code).
- **The handling ANM** gets the creating ANM's access (`write`, `triage`, `read`), so uploads, voice confirmation, AI review and triage work as on an ANM-started case. **Consent stays with the creator:** the handling ANM cannot record or withdraw it (new access mode `consent`). The patient account keeps exactly its earlier permissions; it still cannot submit triage or confirm values.
- **The case code is a bearer secret** (`PT-` + 48 random bits). Anyone holding it, with an ANM account in scope, can take the case over. With facility isolation off, that is any ANM account.
- **Body map** is now stored (`body_map_events`, append-only; latest event wins) so it survives the handover. It is labelled patient-reported, is never triage input, needs triage consent to write, and stays readable after withdrawal (§4 D5). Audit: `body_map_recorded` with a region count only.
- **Limitation:** demo accounts are shared, so every `patient_demo` session can see every case started from `patient_demo`. These rules demonstrate the authorization code, not separation between real patients.

## 4. Withdrawal race contract

| # | Situation | Guarantee |
|:---|:---|:---|
| D2 | Withdrawal committed before an operation starts | The operation is denied (403), with no side effects, and the denial is audited. |
| D3 | Withdrawal while a case-triage transaction runs | The withdrawal waits for the write lock and commits afterwards. The run records the grant it relied on (`consent_seq`). |
| D4 | AI path: withdrawal, or withdraw-and-regrant, of either purpose after authorization | Redaction holds no lock, so the withdrawal commits right away. T1b/T2 compare the consent-event sequence, and if it changed the output is **discarded, not returned or stored** (409 `CONSENT_WITHDRAWN`). An in-flight external call **cannot be cancelled**, and nothing is guaranteed about what an external processor did with it. |
| D5 | Withdrawal after completion | Earlier results are kept (deletion is deferred). New operations are blocked. |

**Rule for future adapters:** subclass `BaseLlmAdapter`, receive only `RedactedText`, pass no side context, and never skip the gateway's T1b/T2 re-checks.

## 5. PII redaction

**Pipeline:**
1. **Normalize:** NFKC; remove format characters (e.g. zero-width space); remove combining marks next to digits; convert every Unicode digit (Devanagari, Odia, fullwidth) to ASCII.
2. **Script policy:** any non-Latin letter → `AI_INPUT_UNSUPPORTED_LANGUAGE`. This fails closed and is reported separately from PII findings.
3. **Analyze:** Presidio runs on the normalized text.
4. **Merge spans:** overlapping or touching spans become their union. The label is the highest score, with ties broken by priority AADHAAR > ABHA > PHONE > PAN > EMAIL > DOB > PERSON > LOCATION.
5. **Replace:** from the end of the string backwards. Spans always index the normalized string, which is the one being redacted.
6. **Check the output:** run the pattern recognizers again and do a residual sweep (≥8 digits joined by short separators; ≥7 spelled-out number tokens in English or romanized Hindi; ≥8 digits counted across digit/number-word runs that include filler tokens such as "dash", "gap", single letters, and "double"/"triple" repeaters — added after the final security review found filler-word bypasses; spelled-out numbers interrupted by up to 4 ordinary words once ≥3 number words are seen; spoken-style emails such as "a dot b at example dot com" — both added after the pre-push review; `@abdm`/`@sbx`). Any hit → `PII_DETECTED`, and the request is blocked. A long run of bare, unlabelled numbers is also blocked (over-blocking is accepted).

Plain ASCII clinical text is passed through byte-for-byte: this is tested with "BP 118/76, HR 110 bpm, RR 22/min, SpO2 97% on air, temp 38.9 C, fever for 3 days…".

| Recognizer | Basis |
|:---|:---|
| Presidio `PhoneRecognizer` (regions IN, US, GB, via the phonenumbers library) | source-supported |
| Email | source-supported |
| `IN_PAN` (built-in, enabled) | source-supported pattern |
| `AADHAAR_LIKE` (12 digits, contiguous or 4-4-4, **no checksum**) | heuristic and fail-safe. A match ≠ a valid Aadhaar; nothing is verified. |
| `ABHA_NUMBER` (14 digits, contiguous or 2-4-4-4), `ABHA_ADDRESS` (`@abdm`/`@sbx`) | heuristic; secondary sources only (the official ABDM page couldn't be fetched) |
| `IN_MOBILE` | heuristic |
| `DOB` (full dates that include a year) | heuristic |
| `PERSON`/`LOCATION` via spaCy `en_core_web_sm`, plus context-cue name patterns (honorifics, "Patient", "name is") | **insufficiently validated; weak for Indian names** |

**Known false negatives:** lowercase or uncued names such as "rameshwar sahoo came with fever" or "brought by pinky" are missed, and so is a date of birth written in Roman numerals ("XII/III/MCMXC"). `xfail` tests document them. **These are weaknesses, not passes:** name detection does not meet a deployment standard.

**Exceptions:** all raw-text handling happens inside `_process_raw`, which returns a reason code instead of raising. The sanitized `PiiRedactionError` is raised only after that frame has returned, and has no `__context__` or `__cause__`. This **reduces** how much raw text stays referenced by exceptions and logs. It does **not** guarantee that no in-memory copy exists; for example, the caller keeps its own reference.

**Dependency note:** `presidio-anonymizer` is intentionally not installed, because it would downgrade `cryptography` and break pyopenssl/azure. Span replacement is done in project code.

## 6. Audit log

- **Row contents:** `seq`, `event_id`, `timestamp`, `actor_id`, `actor_role`, `action`, `case_id`, `outcome`, `request_id`, typed `details` and a SHA-256 chain (`previous_hash` + canonical row → `current_hash`; genesis `"0"*64`).
- **Atomic with the change:** events are written inside the same transaction as the change they describe. If the audit write fails, the change rolls back.
- **Personal data:** details avoid direct identifiers and raw clinical content by design (counts, rule IDs, enums). But `actor_id`, `case_id`, `request_id` and timestamps **make events linkable to accounts and cases and may still be personal data**.
- **Access:** only supervisors can read, and reads omit `actor_id`. There is no update or delete endpoint.
- **Retention:** 1 year is the target policy (doc 04). It is **not enforced in Phase 3**, because the append-only triggers block deletion. An archival migration is future work.
- **Append-only:** SQLite triggers reject UPDATE and DELETE on `audit_log`, `consent_events` and `triage_runs`.
- **What verification catches:** `POST /audit/verify` detects edited rows, deleted middle rows, broken links and altered hashes. It writes one `audit_verified` event, which the next verification covers.
- **Limits:**
  - a database-file administrator can drop the triggers or rewrite the file;
  - **removing the newest rows can't be detected** without an external checkpoint (future work);
  - the whole thing is tamper-evident, not immutable.

## 7. Threat model (summary)

| Asset | Entry point | Threat | Mitigation | Test | Residual risk |
|:---|:---|:---|:---|:---|:---|
| Free text | future AI path | raw text reaches an LLM | one gateway, `RedactedText`-only adapters, fail closed | `test_gateway`, `test_boundary` | missed names (small NER model) |
| Identifiers | free text | split digits, foreign digits, spelled-out numbers | normalization + residual sweep | `test_pii` | new obfuscation methods |
| Consent | consent API | another account's case, forged actor or method | case-access 404, server-derived fields, `extra="forbid"` | `test_authz`, `test_consent` | shared demo accounts |
| Consent | concurrency | withdrawal races processing | check inside the transaction, sequence re-checks, no lock held during redaction | `test_case_triage`, `test_gateway` | in-flight external work |
| Audit | DB and API | edits, injected text, over-collection | triggers, hash chain, UUID-only request ID, typed details | `test_audit`, `test_logging` | file-level admin access; tail truncation |
| Logs and errors | exceptions, validation | PII in logs or responses | `SafeErrorMiddleware`, reason codes, fixed messages, `hide_input_in_errors`, quiet third-party loggers | `test_logging`, `test_pii` | future integrations unreviewed |
| Notice | UI | wrong-language read-aloud; unreviewed text | voice must match the language or show "unavailable"; draft banner | manual | unreviewed translations |
| Accounts | login | shared, password-less accounts | startup guard (dev/test only), UI banner | `test_logging` | **blocks real deployment** |

## 8. API summary

| Method | Path | Roles | Notes |
|:---|:---|:---|:---|
| GET | `/consent/notice?language=en\|hi\|or` | any authenticated | version + review status |
| POST | `/cases` | patient, anm | `{scenario, facility_code}` |
| GET | `/cases/{id}` | creator, medical_officer, supervisor | effective consent per purpose |
| POST | `/cases/{id}/consent` | creator (patient, anm) | `{decision, include_ai_assist, language, notice_version}` |
| POST | `/cases/{id}/consent/withdraw` | creator (patient, anm) | `{purpose}` |
| GET | `/cases/{id}/consent` | creator, medical_officer, supervisor | history (roles only) |
| POST | `/cases/{id}/triage` | creating ANM, medical_officer | `TriageInput`; needs `triage` consent |
| GET | `/audit/{case_id}` | supervisor | no `actor_id` |
| POST | `/audit/verify` | supervisor | chain verification |

**New error codes:**

| Status | Code |
|:---|:---|
| 403 | `CONSENT_REQUIRED` |
| 409 | `CONSENT_WITHDRAWN` |
| 409 | `NOTICE_VERSION_STALE` |
| 409 | `NOTHING_TO_WITHDRAW` |
| 409 | `SCENARIO_MISMATCH` |
| 422 | `PII_DETECTED` |
| 422 | `AI_INPUT_UNSUPPORTED_LANGUAGE` |
| 502 | `AI_ADAPTER_ERROR` |
| 503 | `PII_REDACTION_UNAVAILABLE` |
| 500 | `INTERNAL_ERROR` |

## 9. Sources and legal status

- **DPDP Act 2023:** consent must be "free, specific, informed, unconditional and unambiguous with a clear affirmative action" (s.6(1)). Withdrawal must be comparable in ease to giving consent (s.6(4)). The notice must be in English or an Eighth-Schedule language (s.5(3)). Medical-emergency legitimate use is s.7(f); emergency processing is **deferred**.
- **DPDP Rules 2025:** notified November 2025 with an 18-month phased compliance period (per MeitY/PIB listings). The official pages returned 403 to automated fetches. Per-rule commencement dates, and whether any timeline was shortened, are **unresolved**; check the Gazette.
- **Aadhaar:** Aadhaar Act s.29(4) and the Sharing of Information Regulations 6(1)/6(3) forbid public display unless the number is redacted. This prototype only detects and redacts Aadhaar-like numbers; it never collects, stores or verifies them.
- **Presidio 2.2.364:** documentation at [presidio.dataprivacystack.org](https://presidio.dataprivacystack.org/). India recognizers are disabled by default, and the built-in Aadhaar recognizer requires a Verhoeff checksum (not used here, to fail safe).
- **Web Speech API (MDN):** `speechSynthesis` voices depend on the device. Chrome's `SpeechRecognition` processes audio on a server.
- **OWASP Logging Cheat Sheet:** do not log session IDs, tokens or sensitive personal data.
- **SQLite:** the transaction behaviour relied on here was checked by experiment:
  - DDL and `user_version` roll back together;
  - `executescript` commits any open transaction;
  - `PRAGMA foreign_keys` is ignored inside a transaction.

## 10. Tests

`backend/tests/privacy/` holds 133 tests plus 5 xfails:

| File | Covers |
|:---|:---|
| `test_migrations` | migrations |
| `test_logging` | logging and error hygiene |
| `test_audit` | audit chain and access |
| `test_consent` | consent state rules |
| `test_authz` | case access |
| `test_case_triage` | D2, D3, D5 |
| `test_pii` | redaction |
| `test_gateway` | A, D4, I |
| `test_boundary` | static boundary checks |

All data is synthetic. The 250 Phase 1–2 tests still pass. Browser read-aloud and the consent screens were checked manually; there are no automated frontend tests.


## Phase 4 addendum — voice (see [docs/12](12_Voice_Pipeline.md))

- **Third consent purpose `voice_cloud`** (notice `2026-09-30.3`): sending the patient's voice to Sarvam AI for online speech-to-text, and the heard values for spoken read-back. The checkbox states retention (up to 30 days) and possible model training. Opt-in; effective only while `triage` is granted; omitted = declined; withdrawing `triage` cascades. Migration 3 rebuilds `consent_events` (CHECK constraint) with foreign keys off and `foreign_key_check` before commit.
- **Notice change**: "No voice recording is stored" became "SEHAT AI does not store the voice recording itself", plus a plain statement that Sarvam may keep audio up to 30 days and may use it for training unless the deploying organisation opts out (not verified by this software).
- **Notice `2026-10-01.1`** (supersedes the Sarvam wording above): re-checked against Sarvam's privacy policy (last updated 2026-07-29). The notice now says content is kept for an account-set period (default 30 days after last use, so "up to 30 days" was not a limit), may be used for training unless the account opts out, and may be processed outside India. Earlier consent records keep their original notice version. See docs/12 §1 and §3, and docs/13 for the facts each language must state.
- **Audio**: in process memory only, bounded read, never persisted or logged by this code. Transcripts are stored as untrusted text under triage consent. They are visible to the case creator and MOs (not supervisors), and only while triage consent is in effect: reads after withdrawal are refused and audited.
- **Consent before egress**: checked before any cloud upload; the post-call re-check only discards the result — audio already sent cannot be recalled.
- **Audit**: `voice_transcription_started` (committed before any engine runs), `voice_transcribed`, `voice_transcription_failed`, `voice_readback_resolved`, `voice_tts_generated` — ids, categories and counts only.
- **Bystanders**: a live microphone may capture other people; no speaker separation.

## Phase 5 addendum — documents (see [docs/14](14_OCR_Pipeline.md))

- **No new consent purpose**: OCR runs only on this server (PaddleOCR in-process; Surya OCR 2 and Chandra OCR 2 in a local worker on a 0600 Unix socket). Triage consent is required **and** must have been given under notice `2026-10-01.2` or later, which says a picture of each page and the text read from it are kept with the visit's record, read on this system only, and may show the patient's name. Older consent → `403 CONSENT_NOTICE_UPDATE_REQUIRED`, re-consent.
- **Stored**: re-encoded page PNGs (metadata stripped) under `data/documents/` (git-ignored), OCR text, boxes, fields and decisions. The original upload is never stored (SHA-256 only). **Not redacted**: identifiers printed on the page. **Not deleted**: all `ocr_*` tables are append-only; retention/erasure is not implemented.
- **Access**: content visible to the case creator and MOs, only while triage consent is in effect; supervisors get 404. Upload is ANM-only in Phase 5.
- **Audit**: `ocr_document_started/processed/failed`, `ocr_attestation_recorded`, `ocr_review_resolved` — ids, enums, counts, size buckets only (canary-tested).
- **Logging fix (affects every phase)**: `aiosqlite` logged SQL statements with their parameters at DEBUG, which would include case data, voice transcripts and OCR values whenever `LOG_LEVEL=DEBUG`. It is now held at WARNING with `rapidocr`, `httpx`, `python_multipart` and `PIL` (`app/main.py`).

## Local-model and guardrails addendum (2026-10-05; see [docs/16 §2a–§2b](16_LLM_Extraction_MAKER.md), [docs/18 §4a](18_Medical_Image_Pipeline.md))

- **`AI_PROVIDER=local` keeps case text on this machine, and still redacts it first.** The same gateway, `RedactedPrompt`-only contract and T1/T1b/T2 consent re-checks apply; `ai_assist` consent is still required. Local is not a reason to skip redaction: the model, its server and its logs are still a processor.
- **Server hardening (`scripts/start_local_llm.py`, checked live):** listens on `127.0.0.1` only (`LOCAL_LLM_URL` is restricted to loopback in code and is never echoed); a fresh random API key in a `0600` file (no key → 401); `--no-slots` (the `/slots` endpoint can show prompt text; it answers 501); no web UI; `--offline`; no `-v` (verbose mode logs prompts); no host-RAM prompt cache (the last redacted prompt of each slot still sits in the server's KV memory until the slot is reused or the server stops; it is never written to disk). After a 20-case run the server log contained **no** case words (grep for every distinctive term: 0 hits). httpx ignores proxy variables (`trust_env=False`), so a `HTTP(S)_PROXY` setting cannot route redacted text off the machine. Every reply must name the pinned model alias.
- **Model files:** pinned revision + SHA-256 checked by the download script and again before the server starts. The backend refuses to start with `AI_PROVIDER=local` if the manifest, revision or file size do not match.
- **NeMo Guardrails telemetry:** nemoguardrails 0.24.1 sends usage statistics to `events.telemetry.data.nvidia.com` **by default** (it states no user content is sent). SEHAT sets `NEMO_GUARDRAILS_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1` in code before import; a test checks the package's own opt-out function, and another blocks sockets and asserts the rails make no connection. The rails see redacted text only and use no LLM or embedding model.
- **Audit:** guardrail blocks are recorded as `ai_request_blocked` (input) or `ai_output_discarded` (output) with `reason_code=guardrail_<reason>` only; nothing is stored for a blocked request.
- **IndicTrans2:** startup now re-hashes every file recorded at download time, including the three `trust_remote_code` Python files, and refuses to load a changed or missing file.
- **Not changed:** cloud backends still need `AI_CLOUD_ENABLED=1`, `AI_CLOUD_SYNTHETIC_DATA_ONLY=1` and per-request consent/attestation. Nothing falls back from local to cloud or to the fake.
