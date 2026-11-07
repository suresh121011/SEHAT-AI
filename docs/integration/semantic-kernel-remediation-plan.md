# Semantic Kernel / Gemini Live branch: investigation and remediation plan

> Prepared 2026-11-07 on `integration/semantic-kernel-remediation`, branched from `main` 4b44cc8 (after PR #12).
> **Status: plan only, awaiting approval. No remediation code has been written.** Revision 2: corrected after a six-perspective review (§12).
> Contributor branches investigated:
> - `origin/feature/gemini-live-duplex-followup`: b1e5656 (niranjanswain067, 2026-11-04)
> - `origin/feature/semantic-kernel-plugins`: b1e5656 + 69071c4 + **21a9761** (21a9761 was pushed 2026-11-07 19:51, during this review)
>
> Neither branch has a PR. The original branches will not be modified. Priority order for every decision:
> security → consent → clinical safety → architecture → correctness → maintainability → features.

## 1. Current architecture and pipeline (main 4b44cc8, traced in code)

| Stage | Where | Rule |
|---|---|---|
| Auth | `app/auth.py` JWT in an **httpOnly** cookie set by the Next.js `/api/session` route. The browser reaches the backend only through the same-origin proxy `/api/backend/[...path]`. | JavaScript never sees the token. |
| Consent | `app/consent.py`: per-case, per-purpose, append-only events (`triage`, `ai_assist`, `voice_cloud`, `link_visits`). Notice text in `consent_notice.py`. `voice_cloud` names **Sarvam AI** only. | Consent is checked before any cloud call (docs/12 §1). |
| Voice | Browser WAV clip → `POST /cases/{id}/voice/transcriptions` → Silero VAD → IndicConformer (local) or Sarvam (needs `voice_cloud`) → deterministic number extractor → read-back confirmation | No fallback between engines. The transcript is stored; the audio is not. |
| Extraction | `app/ai/extract.py` through the privacy gateway (Presidio redaction, English only, fail closed). MAKER voting, grounding to quotes. Raise-only urgency **suggestion**. | Only redacted text reaches a provider. Cloud needs `AI_CLOUD_ENABLED` + `AI_CLOUD_SYNTHETIC_DATA_ONLY`. |
| Triage | `POST /cases/{id}/triage` → `rules.engine.evaluate_triage` | **The only urgency authority.** GREEN must be earned: a red-flag screen that was not completed floors the result at YELLOW. |
| Verification, summary, review | OCR verification queue (P0-2), encounter summary (template prose + sentence gate), MO sign-off | Human sign-off is required. Every fact links to its source. |
| Audit | `audit_log`, hash-chained | Details hold ids and enums only. |
| Privacy guard tests | `tests/privacy/test_boundary.py`, `tests/ai/test_api.py` | Pin which routes accept free text and which code may invoke an LLM. |

## 2. What the contributor branches add

| Commit | Content |
|---|---|
| b1e5656 | Gemini 2.0 Flash **Multimodal Live** duplex voice follow-up (hi/or):<br>• backend WebSocket `/cases/{id}/voice/live-followup` → Google `BidiGenerateContent`<br>• the model asks for missing fields and submits them by function call into a `pending_review` table<br>• migration "step 13" (`gemini_live_sessions`, `gemini_live_fields`) and 3 audit actions<br>• frontend `DuplexVoiceFollowUp.tsx` on the follow-up page, plus a new Next.js route `/api/session/token`<br>• docs/19 (Qdrant/R2 RAG) and docs/20 |
| 69071c4 | Semantic Kernel registers Azure, **Google Gemini (OpenAI-compatible endpoint)** and local services. Four plugins: TriageRules, ClinicalHandoff, PatientContext and SafetyGuard. |
| 21a9761 | `services/orchestrator.py` and **`POST /cases/{id}/ai/orchestrate`**. The route runs the plugins end to end and returns urgency and a handoff note. |

**Worth keeping** (the user asked that useful work not be rejected without investigation):
- The idea of capturing missing fields as `pending_review` rows, and the Hindi/Odia field-normalisation map.
- Multi-provider registration in the kernel (matches the Cognizant / Semantic Kernel framing in AGENTS.md).
- The typed plugin wrappers over the deterministic engine, as a test and evaluation harness.
- The contributor's tests, as specifications of intent.

## 3. Findings, all verified in code or by test

### A. Test failures (the branch tip 21a9761 alone fails 4 tests; 1304 pass)

| Test | Root cause | Kind |
|---|---|---|
| `tests/ai/test_redflag_keywords.py::test_migration_11_…` (expects 12, gets 13) | The branch added a migration and updated `test_migrations.py`, but not this second version assertion | Test not updated |
| `tests/ai/test_api.py::test_only_two_request_fields_carry_free_text` | `/ai/orchestrate` accepts unconstrained `intake_text` and `source_label` | **A real privacy-boundary violation**, not a test problem |
| `tests/privacy/test_boundary.py::test_no_route_accepts_free_text_for_ai` | Same route: raw intake text sent to AI without the gateway's free-text rules | **Real violation** |
| `tests/privacy/test_boundary.py::test_only_kernel_smoke_test_invokes_the_llm` | `orchestrator.py` calls `kernel.invoke` | A violation of the pinned "who may call an LLM" rule. The calls are to native plugins, so **no LLM is actually called**; the rule still forbids invoking outside the smoke test. |

Three of the four failures are security controls doing their job. They must not be relaxed to get a green build.

### B. Merge conflicts with main 4b44cc8 (5 files, not 4: PR #12 added one)

| File | Main side | Branch side | Proposed resolution |
|---|---|---|---|
| `AGENTS.md` | docs/19 Encounter Summary | docs/19 Qdrant RAG, docs/20 Gemini Live | Keep main's docs/19. The Qdrant doc is not carried (§3F). Any voice doc that survives is renumbered after the current last doc. |
| `backend/app/audit.py` (action list) | encounter summary and patient actions | 3 gemini_live actions | Keep main's. Add only the actions for features that survive §7, with enum-only details. |
| `backend/app/audit.py` (ConsentPurpose) | adds `link_visits` | does not | Keep main's `link_visits`. |
| `backend/app/database.py` | steps 13 (encounter summary) and 14 (patient registry) | its own step 13 (gemini tables) | See §3C |
| `backend/tests/privacy/test_migrations.py` | version 14 | version 13 | Recomputed from the final migration list |
| `backend/app/routes/ai.py` | encounter-summary and verification-queue routes | `/ai/orchestrate` | Keep main's. `/ai/orchestrate` is not carried as is (§3F). |

### C. Migration "step 13" collision

- Migrations run in order and are tracked by `PRAGMA user_version` (`database.run_migrations`). Main's step 13 creates `encounter_summaries`. The branch's step 13 creates `gemini_live_*`. Both cannot be step 13.
- **Hazard (corrected by the database review):** a local database already migrated by the branch reports version 13. On main it skips main's step 13, then step 14 **succeeds**, because it never references `encounter_summaries`. The database ends at version 14 with no summary tables and with leftover gemini tables. The failure is silent and shows up later, when summary routes fail with "no such table". The PR #12 description warns users to rebuild such databases.
- **Plan:**
  - Never renumber main's applied steps.
  - Any surviving table is added as a new forward step **15**, using the append-only helper (§3F.4).
  - A guard inside `init_db`, **before** `run_migrations`, raises a dedicated `SchemaMismatchError` (fail fast, with rebuild instructions; not a warning). It fires when a database at version ≥ 13 lacks `encounter_summaries`, **or** contains `gemini_live_sessions`. It must run before migrating, because step 14 would otherwise rebuild `consent_events` first.
  - Under D1 = b, step 15 uses **new table names**, so a leftover mutable contributor table can never be mistaken for the new append-only one.
  - Tests use disposable databases built from `MIGRATIONS[:12]` plus the contributor's step-13 DDL, copied into the test (the test cannot import the contributor branch). Such a database must be refused with a clear message. The schema-version tests keep **one pinned literal**; a value computed from the code could never catch an unplanned migration.
  - `sehat.db` is never touched.

### D. Login token exposed to the browser

- **Traced path:**
  1. `GET /api/session/token` (new Next.js route) reads the httpOnly `sehat_session` cookie and **returns the JWT as JSON** to page JavaScript.
  2. `DuplexVoiceFollowUp.tsx` then opens `ws://<host>:8000/api/v1/cases/{id}/voice/live-followup?token=<JWT>&language=…`, **bypassing the same-origin proxy**, with the **JWT in the URL**.
  3. The backend WebSocket accepts `?token=` or the `sehat_session` cookie and does **no Origin check**.
- **Threat model:**
  - Any XSS, or any third-party script, can read the session token. That defeats httpOnly.
  - JWTs in URLs end up in access logs, proxies and browser history. The token lives up to 1440 minutes (`JWT_EXPIRE_MINUTES`).
  - Because the WebSocket accepts the cookie and does not validate Origin, it is open to **cross-site WebSocket hijacking**: a malicious page could open a live session on the user's behalf. Browsers do not apply CORS to WebSockets.
- **Also found in review:**
  - The Google **API key is sent in the WebSocket URL** (`gemini_live.py:386`, `?key=`), and connection errors are logged with exception text. The key belongs in the `x-goog-api-key` header.
  - Upstream error text is forwarded to the browser (`{"event":"error","message": …}`).
  - The WebSocket accepts the connection before it authenticates.
  - The JWT has no `jti` and no revocation, so any token already leaked through the route stays valid for up to 24 hours. For any build that shipped the route, rotate `JWT_SECRET_KEY` or shorten the expiry.
- **Plan, if any WebSocket feature survives:**
  - Delete `/api/session/token`.
  - The backend issues a **single-use ticket**, valid about 30 seconds and bound to user + case + purpose, via a POST through the same-origin proxy. The ticket is stored as a database row consumed atomically (`UPDATE … WHERE used = 0`), not in a per-process dict.
  - **The WebSocket itself cannot travel through the Next.js proxy**: route handlers do not upgrade connections, and there are no rewrites. So a same-origin path (a reverse proxy in front of both servers) or an explicit public `wss` URL with TLS is required. The owner must choose this (decision D7).
  - The client sends that ticket as its **first WebSocket message**, never in the URL.
  - The WebSocket validates an `Origin` allowlist, rejects cookie-only auth and query tokens, and the ticket is consumed on use.
  - **Tests:** the token route is gone, a `?token=` query is rejected, a bad Origin is rejected, a replayed ticket is rejected, a ticket for another case is rejected, and no JWT appears in any URL the frontend builds.
- **If no WebSocket survives:** delete the route, and no WebSocket endpoint is added.

### E. Consent: Sarvam-only consent used for Google audio

- **Traced path:** the live route checks `voice_cloud`, whose notice and checkbox name **Sarvam AI** (`consent_notice.py`, docs/12 §3), and then streams raw 16 kHz patient audio to **Google** `generativelanguage.googleapis.com`.
- **Missing gates:**
  - It checks neither `AI_CLOUD_ENABLED` nor `AI_CLOUD_SYNTHETIC_DATA_ONLY`, nor any per-session synthetic-data attestation. The existing Google image path requires all three (docs/18 §4).
  - `kernel.py` registers a Google chat service whenever `GOOGLE_AI_API_KEY` is set, without `AI_CLOUD_ENABLED`.
  - There is no silent fallback between providers, which is correct.
- **Plan:**
  - Audio may reach Google only under a **separate, provider-named consent purpose** (e.g. `voice_cloud_google`), with notice text that names Google and its data handling. That text needs the owner's approval and docs/13 language review; the existing wording will **not** be changed to fit the code.
  - Audio to Google also requires the cloud flag, synthetic-only mode and a per-session attestation.
  - If the consented provider is unavailable, fail closed.
  - **Tests:** a Sarvam-only consent can never open a Google session; a mocked transport proves zero bytes are sent without the Google purpose; the kernel registers no cloud service without `AI_CLOUD_ENABLED`.

- **Also found in review (privacy):**
  - **Audit fails open.** If the session row cannot be written, the error is only logged and audio still streams to Google, with no session row and no audit entry.
  - **Spoken output is never checked.** The non-diagnostic filter runs only on text events; the model's audio goes straight to the patient. That alone blocks D1 = b until the spoken output is constrained.
  - **Consent is read once** at session start, so a withdrawal does not stop a running stream.
  - **`patient_phrase` is model-written text** attributed to the patient, not a transcript. Under D1 = b it must not be stored (field, value and unit only).

### F. Departures from the agreed architecture

| # | Finding | Rule broken |
|---|---|---|
| 1 | **The orchestrator hard-codes `red_flag_screen_completed: True` and `red_flags_present: []`**, with default `age_years=30`, then returns `effective_urgency`. **Worse:** if triage throws, the plugin returns `{"error": …}`, and the orchestrator falls back to `.get("final_urgency", "GREEN")`, so **a failure is reported as GREEN**. Its raise-only call passes the same value twice, so it can never change anything. Its only test uses a RED case, so it could never catch either defect. | GREEN must be earned (architecture §11, docs/10). An unscreened patient with normal vitals can be returned as **GREEN**. **Clinical-safety defect.** |
| 2 | `/ai/orchestrate` is a second path that produces urgency, outside the triage form, with no triage run and no audit | The rules engine via `POST /triage` is the only recorded authority. The new path creates a duplicate engine entry point. |
| 3 | Gemini Live: the model decides what to ask next and receives raw audio; there is no ground-truth transcript; captured fields have no review endpoint and never reach triage or the summary | The architecture audit (D4) and the plan council: no native speech-to-speech for clinical capture; a scripted interviewer instead. The fields are also dead-end data. |
| 4 | `gemini_live_fields`: mutable `status` column, no append-only triggers, stores `patient_phrase`; `gemini_live_sessions` is updated in place | Append-only and finalise-once rule (docs/11) |
| 5 | `logger.exception(... %s, e)` and `str(exc)` returned to the LLM and to callers; audit `field_name` is free text chosen by the model | Logs and audit hold codes, ids and enums only |
| 6 | `PatientContextPlugin` keeps patient narratives in an in-process dict; the orchestrator builds a new kernel on every request | Not persisted, not audited, not access-checked; the store is lost per request anyway |
| 7 | Plugins are designed for autonomous agent use | No autonomous agents at runtime (audit, council) |
| 8 | docs/19 Qdrant / R2 / Mistral RAG | Qdrant was removed from the roadmap. R2 is blocked for real data (no India jurisdiction). Mistral needs a DPA. |
| 9 | The frontend hard-codes backend port 8000 | Breaks the same-origin proxy model and every non-local deployment |
| 10 | Every follow-up page shows the Gemini card, with no frontend flag. It opens the microphone before the server can refuse. It tells the ANM the answers "automatically populate the form" and are "auto-saved to case record", but the fields never reach the form. | Honest UI; consent before capture |
| 11 | `services/kernel.py` becomes a **third or fourth** Semantic Kernel entry point: `azure_provider.py` and `medgemma.py` each build their own `AzureChatCompletion`. Registering Gemini reuses the image key and turns `/health` `llm_status` to "configured", which misreports the extraction LLM. | One provider factory; honest health |

## 4. Can Semantic Kernel fit without competing with the rules?

Yes, but only as **plumbing**. `app/ai/azure_provider.py` already uses Semantic Kernel, so it is already the integration point.

- Keep multi-provider registration **only if** `AzureProvider` and the MedGemma Azure backend move onto the same factory at the same time (decision D6), and only with cloud services registered under `AI_CLOUD_ENABLED`. Otherwise keep `services/kernel.py` as the existing smoke test: a factory with no consumer is another dead path.
- Keep the plugin wrappers **outside the request path**: as a typed harness for evaluation and red-team tests (for example, "any LLM suggestion through `apply_raise_only_urgency` can never lower urgency").
- Do **not** keep an orchestration route that returns urgency, and do **not** run runtime agents. The existing deterministic pipeline (extract → verify → triage → summary) already does each orchestration step, with consent, audit and source links. That avoids a second, weaker copy.

## 5. Proposed target, smallest change that fits the architecture

1. **Carry, after rework:**
   - The kernel's multi-provider factory, behind the cloud flags.
   - The plugins as test-only wrappers, with `str(exc)` removed.
   - The contributor's kernel tests, adapted.
   - The Hindi/Odia field-normalisation map, kept as a fixture for a later interviewer.
2. **Do not carry:**
   - `/api/session/token`
   - `/ai/orchestrate` and `orchestrator.py`
   - `PatientContextPlugin`'s in-memory store
   - docs/19 Qdrant
   - the Gemini Live WebSocket, frontend component and tables (pending decision D1)
3. **Decision D1, Gemini Live:**
   - (a, **recommended**) Do not carry it. Record the field-capture idea as input to the planned scripted voice interviewer (L3-1).
   - (b) Carry it as a **research-only, synthetic-data** feature. This requires all of §3D (ticket auth), §3E (a Google consent purpose with approved notice text), §3C (step 15, append-only), a review endpoint feeding the existing review flow, and the model never choosing clinical questions outside a fixed list. This is several days of work and still conflicts with D4.
4. **Attribution:**
   - The contributor's commits are merged into the integration branch first, preserving authorship.
   - Every fix is a separate, small commit on top.
   - Removals are commits with a reason in the message.
   - The original branches are untouched.

## 6. Implementation phases (after approval)

| Phase | Work | Depends on |
|---|---|---|
| R0 | Merge the contributor tip (pinned to 21a9761 or a later approved commit) into the integration branch. Resolve the 5 conflicts per §3B. | approval |
| R1 | Write security and safety regression tests first: they must fail on R0 and pass after R1–R4 | R0 |
| R2 | Remove `/api/session/token`, `/ai/orchestrate`, `orchestrator.py`, the in-memory store and the Qdrant doc (D1a: also the Gemini Live route, component, migration and audit actions) | R1 |
| R3 | Kernel: gate cloud services behind `AI_CLOUD_ENABLED`; remove exception text; plugins test-only | R1 |
| R4 | Migration guard for mismatched step 13 (§3C); schema-version tests computed from the code | R0 |
| R5 | (only if D1 = b) Single-use ticket WebSocket auth, Origin allowlist, Google consent purpose, step 15 append-only tables, review endpoint | D1, D2 |
| R6 | Full verification (§8) and a PR to main with the remediation write-up. Not merged until review passes. | R1–R4 (R5) |

## 7. Test plan and security regression cases

- **Existing guards stay green and are not edited:**
  - `test_no_route_accepts_free_text_for_ai`
  - `test_only_kernel_smoke_test_invokes_the_llm`
  - `test_only_two_request_fields_carry_free_text`
  - the migration tests
- **New tests:**
  1. `/api/session/token` does not exist (frontend route test).
  2. No frontend code builds a URL containing `token=`, a hard-coded `:8000`, or a Google `?key=` (static scan test).
  3. No new route's response schema **produces** urgency. This is scoped to producers; the review queue legitimately shows the rules engine's recorded urgency.
  3b. No `red_flag_screen_completed: True` literal anywhere in `app/` (static scan). Harness test: an omitted screen plus normal vitals never yields GREEN, and a triage error is never reported as GREEN.
  4. The kernel registers no cloud service without `AI_CLOUD_ENABLED=1` and synthetic-only mode.
  5. Nothing in `app/` returns or logs `str(exc)` or `%s, e` (static scan, as existing log-canary tests do).
  6. A database migrated to the contributor's step 13 is refused by the guard with a clear message, and a fresh database migrates to the final version.
  7. Plugin harness: `apply_raise_only_urgency` never lowers urgency over every pair of levels. This widens the existing test at `test_kernel.py:74-90`; the harness also covers `red_flag_screen_completed=False`.
  8. Only if D1 = b: the WebSocket rejects query tokens, cookie-only auth, a bad Origin, a replayed ticket and a cross-case ticket; a Sarvam-only consent never opens a Google session; zero bytes are sent without the Google purpose.
- **Full suites:** backend, frontend, `tsc`, ESLint, the migration upgrade tests, and the real-app browser check (22 checks).

## 8. Risks and rollback

| Risk | Mitigation and rollback |
|---|---|
| The contributor keeps pushing (21a9761 arrived during this review) | Pin R0 to a named commit. Re-run this investigation on any newer commit before merging it. |
| Removing work hurts collaboration | Credit the commits, explain each removal on the PR, and offer to pair on the L3-1 interviewer that reuses the field-capture idea |
| A teammate's local database is at the contributor's step 13 | The guard (R4) plus the PR #12 upgrade note; the data is synthetic, so the fix is a rebuild |
| Rollback | The integration branch is merged into main only by PR after full verification. Until then main is unaffected, and reverting the PR merge commit undoes it. |

## 9. Files

- **To modify:** `AGENTS.md`, `backend/app/{audit,config,database,auth}.py`, `backend/app/routes/{ai,voice}.py`, `backend/app/services/{kernel,plugins}.py`, `backend/tests/privacy/test_migrations.py`, `backend/tests/ai/test_redflag_keywords.py`, `backend/tests/services/test_kernel.py`, `frontend/src/app/intake/follow-up/page.tsx`.
- **To remove** (with reasons in commits): `frontend/src/app/api/session/token/route.ts`, `backend/app/services/orchestrator.py`, `docs/19_Qdrant_R2_RAG_Pipeline.md`. Under D1 = a, also `backend/app/voice/gemini_live*.py`, `frontend/src/components/DuplexVoiceFollowUp.tsx`, `backend/tests/voice/test_gemini_live.py`, `docs/20_…`.
- **Must remain untouched:**
  - the rules engine (`backend/app/rules/`)
  - `consent_notice.py` wording, unless D2 is approved
  - existing migrations 1–14
  - the privacy-guard tests
  - the owner's uncommitted WIP (`cds`, `patient`, `surveillance`, `public/`, `frontend/package.json`)
  - the contributor's original branches

## 10. Acceptance criteria

1. Zero failing tests in the full backend and frontend suites, with **no edits to the privacy-guard tests**.
2. `tsc` and ESLint clean.
3. The real-app check passes 22/22.
4. No browser-readable credential, and no credential in any URL.
5. No audio or text reaches Google without a Google-named consent and the cloud and synthetic-only gates. Under D1 = a, there is no Google audio path at all.
6. The rules engine through `/triage` remains the only urgency authority. No hard-coded screen completion anywhere.
7. Every new table is append-only, and logs and audit hold enums and ids only.
8. A migration from a fresh database reaches the final version, and a mismatched database is refused clearly.
9. The contributor's authorship is preserved, and every removal is explained.

## 11. Decisions needed from the owner

- **D1:** Gemini Live: (a) do not carry it (recommended), or (b) carry it as research-only with all safeguards (§5.3).
- **D2:** (only for D1 = b) Notice text for a new Google voice consent purpose, plus its Hindi/Odia review.
- **D3:** Remove `/ai/orchestrate` (recommended), or keep a reworked version. A reworked version could only call the existing pipeline and would return no urgency.
- **D4:** Talk to the contributor before opening a PR built on his work? Recommended: yes, share this plan with him first.
- **D5:** Pin to commit 21a9761, or wait for the contributor to finish pushing.
- **D6:** One Semantic Kernel provider factory used by extraction, MedGemma and health, or keep `services/kernel.py` as a smoke test only (recommended for now).
- **D7:** (only for D1 = b) Accept a non-proxied, cross-origin WebSocket at all? If yes, which public `wss` URL and reverse-proxy setup? The security review recommends that a "no" here means D1 = a.
- **D8:** Add a minimal CI workflow (backend pytest, frontend tests, `tsc`, ESLint) and branch protection on `main` before the remediation PR. Recommended: yes; none exists today.
- **D9:** Rotate `JWT_SECRET_KEY` on any machine that ran a build with `/api/session/token`.

## 12. Council review (six perspectives, 2026-11-07)

The reviews covered architecture, application security, healthcare privacy and consent, backend and database, frontend integration, and testing and reliability. Each reviewer verified claims against the code with file and line references; all of the plan's core findings were confirmed.

**Corrections applied in this revision:**
- The step-13 hazard is silent: step 14 succeeds.
- The guard goes inside `init_db`, before migrating, and also catches leftover gemini tables.
- Schema-version tests keep a pinned literal.
- The "invokes LLM" failure is native plugins, not an LLM call.
- The urgency-route test is scoped to producers.
- The WebSocket cannot use the Next.js proxy.
- Tickets are stored in the database.

**New findings added:**
- GREEN on triage error, and a raise-only call that does nothing.
- The Google API key in the URL, and upstream error text sent to the browser.
- No JWT revocation.
- Audit fails open; spoken output is unchecked; consent is read once; `patient_phrase` is model-written.
- Misleading UI copy and a microphone opened before the server can refuse.
- Duplicate Semantic Kernel entry points, and a misreported `/health`.

**Disagreements, and how they were resolved:**
- **Architecture:** carry the provider factory only with a consumer. Adopted as D6.
- **Privacy:** ban `patient_phrase` even under D1 = b. Adopted.
- **Database:** new table names for step 15. Adopted.
- **Testing:** do not compute the version literal. Adopted.

**Unanimous across reviewers:** D1 = (a) is the safe default. D1 = (b) needs an owner-approved Google consent text (D2), a WebSocket origin decision (D7), checks on spoken output, and the full security set before it could be considered.
