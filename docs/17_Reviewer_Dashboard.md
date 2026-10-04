# 17 · Reviewer Dashboard (Phase 8)

> Research prototype, not a clinically validated device. Synthetic data only. Non-diagnostic: urgency comes from the
> deterministic rules engine (docs/10); AI never orders the queue and may only raise urgency (docs/16).

## 1. What Phase 8 does

A medical-officer workstation at `/dashboard` (one page, `?case=<id>`), with three panes under a RED review-target banner:

| Pane | Content | Source |
|:---|:---|:---|
| Priority queue | Server-ordered cases, urgency filter (hides rows, never re-sorts), waiting time, review status, missing-data and RED-target pills | `GET /triage/queue` |
| Case workspace | Rules result next to the reviewer-recorded urgency, queue position explained, RED target, actions, triggered rules with their clinical source, missing fields, recorded triage input, review history and audit log | `GET /triage/{case_id}` |
| Evidence and explanation | Hypothetical counterfactuals; AI-extracted values with quotes, checked lab-report values with page highlights, voice values confirmed by a person | existing Phase 4–6 endpoints |

The supervisor gets `/dashboard/governance`: aggregates only. The supervisor can also open the queue and cases read-only (`can_review: false`).

Implementation: `backend/app/review_queue.py`, `backend/app/routes/review.py`, `frontend/src/app/dashboard/**`, `frontend/src/components/review/*`, `frontend/src/lib/review.ts`, `frontend/src/lib/useReview.ts`.

## 2. Endpoints (`/api/v1`)

| Method | Path | Roles | Notes |
|:---|:---|:---|:---|
| GET | `/triage/queue?facility_code=&include_signed_off=false` | MO, supervisor | Server order; signed-off cases are hidden by default, but an unacknowledged RED is never hidden |
| GET | `/triage/escalations?facility_code=` | MO, supervisor | RED target states only |
| GET | `/triage/{case_id}` | MO, supervisor | Clinical content (`result`, `input`, missing fields, reason text) only while triage consent is in effect. Urgency and RED state are always shown |
| PATCH | `/triage/{case_id}/sign-off` | MO | `{triage_run_id, confirm: true}` |
| PATCH | `/triage/{case_id}/override` | MO | `{triage_run_id, new_urgency, reason_code, reason_text?, confirm: true}` |
| POST | `/triage/{case_id}/corrections` | MO | `{expected_triage_run_id, input, reason_code, reason_text?, confirm: true}` → 201; a new run bound to the run the reviewer saw (§3a) |
| POST | `/triage/{case_id}/acknowledge` | MO | `{triage_run_id}`; needs no consent (it records only who saw the alert, and when) |
| GET | `/audit/governance?since=&until=` | supervisor, admin | No case ids, tokens, reviewer ids or free text |

docs/06 §2.4 already named the queue, sign-off and override paths. `acknowledge`, `escalations` and `corrections` are new. Override also takes an optional `expected_urgency`; if another reviewer changed the urgency meanwhile it returns `409 URGENCY_CHANGED`.

Errors:
- `400 VALIDATION_ERROR`: missing `confirm`, an unknown reason code, or `other` with less than 10 characters.
- `409`: `STALE_TRIAGE_RUN` (a newer run exists), `URGENCY_CHANGED`, `NO_STORED_INPUT`, `SCENARIO_MISMATCH`, `ALREADY_SIGNED_OFF`, `ALREADY_ACKNOWLEDGED`, `NO_CHANGE`, `NOT_ESCALATED`, `NO_TRIAGE_RUN`.
- `403 CONSENT_REQUIRED`, or the role is not allowed.
- `422 PII_DETECTED`: the override text contains an identifier.

## 3. Ordering and urgency (rules-first)

- **Order:** priority urgency, RED > YELLOW > GREEN, then the oldest latest triage run first. The UI shows this order unchanged; `serverOrderLooksValid` only logs a dev warning if the order looks wrong.
- **Priority urgency** is the rules urgency. A reviewer override can raise it. An override that lowers it is recorded as the reviewer-recorded (effective) urgency, but the case **keeps its rules position**: lowering never moves a case down. A signed-off case leaves the default queue view. The UI says this beside the case.
- The queue item carries no AI score or confidence (tested), so a failed or low-confidence AI extraction cannot move a case or drop it from the queue.
- Review actions are append-only `review_events` rows bound to **one** triage run. The triage run itself is never modified. A corrected re-run (`POST /cases/{id}/triage`) creates a new run and starts a fresh review. Earlier runs, overrides and sign-offs stay in the history. An action on a superseded run returns `409 STALE_TRIAGE_RUN`; the UI shows the message and reloads, and never retries silently.

## 3a. Reviewer corrections (hardening pass, 2026-10-04)

"Correct vitals" calls `POST /triage/{case_id}/corrections` (MO only). In **one** `BEGIN IMMEDIATE` write transaction the server:
1. checks case access and triage consent;
2. refuses with `409 STALE_TRIAGE_RUN` (and `details.latest_triage_run_id`) if the run the reviewer saw is no longer the latest;
3. refuses with `409 NO_CHANGE` if nothing changed ("to confirm the recorded values, sign off instead");
4. re-runs the deterministic rules through the **same** function as intake triage (`case_triage.record_run`);
5. records a new append-only run carrying `corrects_run_id`, `correction_reason_code` and `correction_reason_text`, plus a `triage_recorded` and a `triage_corrected` audit event. The audit event holds ids, field paths and enums only, never values or free text.

The earlier run is never modified. A partial unique index allows each run to be corrected at most once, so two concurrent corrections cannot both commit. This is tested with two real database connections racing: exactly one commits, the other gets 409.

Correction reasons (prototype list, **not clinically approved**): `remeasured` "Re-measured with the patient", `entry_error` "Typed in wrong when recorded", `source_disputed` "Source of the recorded value is doubtful", `other` (≥10 characters of explanation). The explanation (≤300 characters) gets the same identifier check as override text.

**Per-field provenance:** `latest.field_provenance` is built only from what the server recorded.
- For a correction run, each changed field shows `reviewer_correction` (previous value, reason, role, time). Every other field shows `unchanged_from_previous_run`.
- For an intake run the map is `{}`: intake submits one combined input, so who or what measured each value is not stored. It is never guessed, and client-supplied provenance is not accepted.

`corrections[]` gives each correction's old → new values. Values and free text are hidden when triage consent is withdrawn; the fact of the correction stays.

## 3b. Intake re-triage concurrency (final hardening, 2026-10-04)

`POST /cases/{id}/triage?expected_run_id=<run id | none>` (intake triage; it also creates runs for an MO, as docs/06 allows).
- The first run of a case needs no token.
- Once a run exists, the request must name the run it was based on.
- A missing token returns `409 EXPECTED_RUN_REQUIRED`. A token that is not the latest run (including `none` when a run exists) returns `409 STALE_TRIAGE_RUN` with `details.latest_triage_run_id`.
- The check runs inside the same `BEGIN IMMEDIATE` write transaction as the insert, so it holds across connections and processes on the same database file. Nothing is written on refusal.
- `GET /cases/{id}` exposes `latest_triage_run_id` (an id only, no clinical content) so the intake screen can send it. On a 409 the intake screen tells the user to stop and reload, and never resubmits automatically.

Tests in `tests/review/test_retriage_concurrency.py`:
- two real connections racing re-triage against re-triage, and re-triage against a reviewer correction: exactly one commits;
- stale and missing tokens;
- a malformed token;
- failed transactions (scenario mismatch, consent withdrawn) write nothing;
- an override on the earlier run stays in history and a reviewer-raised RED carries over;
- a lower re-triage keeps an open RED.

A re-triage based on the latest run is still allowed after a reviewer's override on that run: the new data starts a fresh review, the override stays in the history, a RED carries over (§5), and the reviewer's next action on the superseded run is refused with 409.

## 4. Override reason codes (prototype list)

| Code | Label |
|:---|:---|
| `clinical_reassessment` | Clinical reassessment on examination |
| `data_entry_error` | Recorded input was entered incorrectly |
| `additional_information` | Additional information not captured in the triage input |
| `source_disputed` | Source evidence is disputed or unreliable |
| `other` | Other (explanation required, ≥10 characters) |

- This is not a clinical taxonomy. It is pending clinical-governance review.
- The UI shows only the codes the server returns (`override_reasons`).
- An override is distinct from a data correction. The UI points wrong values to "Correct vitals", which re-runs the rules.

## 5. RED review target ("3-minute timer") — what it is and is not

- Deadline = anchor + 180 s, computed on the server.
- The anchor is the time of the RED triage run, or the override time when a reviewer raised the case to RED.
- **Re-running triage neither restarts nor closes the clock.** An earlier rules-RED run that nobody acknowledged keeps the escalation open on every later run, RED or not. The anchor is the earliest such run (`anchor_source: earlier_red_run`). This also covers an earlier run that a reviewer raised to RED by override. So RED→GREEN, RED→GREEN→RED, or "Correct vitals" after a reviewer raise cannot hide or reset it. Agent D and the final council (Contrarian) found these gaps; both are fixed and tested.
- Only an acknowledgment or a sign-off on the latest run closes it. This now also holds when a reviewer raised a case to RED and a later override lowered it again: the escalation stays open until acknowledged.
- A case with a RED escalation keeps **RED queue priority** (`priority_source: open_red_escalation`) until the run is **signed off**, whatever the latest run or a later override says. Acknowledging ("seen") stops the timer but does not lower queue priority, so a reflexive acknowledgment cannot bury the case (final council).
- One episode walk (`walk_escalations`) drives the queue state, the carry-over across re-runs and governance. If a reviewer acknowledges a RED and later raises the same run to RED again, that is a **new** escalation with its own deadline, and it needs its own acknowledgment. The earlier one-acknowledgment-per-run unique index was dropped (migration 9); duplicate acknowledgments of the same escalation are still refused (`409 ALREADY_ACKNOWLEDGED`) by the state check inside the write transaction.
- States are `pending`, `overdue` and `acknowledged`. The first time any queue or escalations read sees a case overdue, the server records **one** `red_escalation_overdue` audit event per escalation. It is keyed by case and server deadline, so re-runs do not repeat it.
- **No notification channel exists**: `notification_sent: false` always. The banner says "No SMS, call or alert is sent by this prototype — this screen is the only indicator." This is a displayed target, **not** an escalation service. If no one has the dashboard open, nothing happens. That is a P0 gap before any real use (§8).
- **Client countdown:** the remaining time is `deadline − (client now + server offset)`, using `generated_at` from the last response. It survives a reload and never restarts. If the deadline passes before the next 15 s refresh, the row shows "Past target … server confirmation on next refresh" (`past_target`). It does not claim the state is overdue until the server says so.
- **Screen readers:** a polite live region announces state changes only (new RED, past target, overdue), never every second.
- **Acknowledge** means "seen", not "reviewed", and the UI says so. It is offered only inside the open case, not in the banner, so a reviewer cannot dismiss a case without looking at it.

## 6. Design decisions

The palette extends the Phase 7 tokens (`globals.css`). All contrast ratios below were computed:

| Element | Colours | Contrast |
|:---|:---|:---|
| Shell | `#0a3f40` / `#ffffff`; muted `#c9e4e1` | 11.7, 8.7 |
| Focus on the shell | White ring (the navy ring would be 1.1:1) | 11.7 |
| Selected queue row | `#e3f0ee` with a 4 px `#0b5d5e` bar | 12.6 |
| RED target, pending | `#fdecea` / `#8c1d18`, clock icon | 8.0 |
| RED target, overdue | `#8c1d18` / white, alert icon | 9.1 |
| RED target, acknowledged | Neutral grey `#eef2f3` / `#2f3e44`, check icon; never green | 9.8 |
| Human reviewer | `#0b5d5e` on `#e3f0ee`, solid border | — |
| AI assistance | Existing dashed purple | — |
| Hypothetical | Hatched `#f4f3ef`/`#e6e3dc` surface, dashed `#6b7a7f` border, "HYPOTHETICAL" chip, outlined urgency word (never the real urgency fill) | ink 13.2 / 11.5 |
| Charts | `#2166ac` / `#b35806` / `#3d4b52` | 5.9 / 4.9 / 9.0 on card |

Final-pass UI changes (Agent C):
- **Status pill:** one component with four tones, following the GOV.UK and NHS "Tag" guidance (adjectives, never clickable, same tone everywhere). The tones are neutral, attention (amber, e.g. missing information), done (teal; acknowledged and signed-off are never green), and danger (red, RED-related only).
- **Shared components:** a `Panel`/`SectionCard` component and a `WorkstationBar` with a persistent facility line.
- **States:** a skeleton for the queue's first load (Carbon data-table guidance), and a lock-icon permission state.
- **Timeline:** a time column; the audit log is now a table.
- **Typography:** tabular figures for times.
- **Icons:** sizes normalised to 14/16/20.
- **Contrast:**
  - New `--skeleton #e3e7e5` (decorative only, always beside a text label).
  - Facility warning 6.8:1; muted text on the reviewer tint 6.2:1; timeline rail 4.5:1.
  - The dark-red acknowledge button keeps a white ring plus an outer navy ring, so focus shows on both red (9.1) and pink (11.6).
- **References fetched in this pass:** GOV.UK "Tag", Carbon data-table usage, NHS "Tag".
- **ui-ux-pro-max:** its font suggestion (adds a dependency) and "sort bars descending" (governance categories have a fixed order) were rejected.

Other decisions:
- GREEN is always worded "routine queue — not a statement that the patient is well".
- Confirmations of a recorded action use the neutral info tone, not green.
- Sign-off says: "It records that you reviewed this triage run. It is not a diagnosis and does not mean the patient is safe."

References actually fetched during design research (Agent A):
- GOV.UK Design System: notification banner, warning text, summary list
- NHS service manual: care cards, colour
- AHRQ PSNet: alert fatigue primer
- Carbon: status indicator pattern
- USWDS: table
- WAI-ARIA APG: modal dialog

Figma Community hospital dashboards were seen as search snippets only (direct fetch returned 403). The `ui-ux-pro-max` search returned an off-topic landing-page and cyan palette, which was rejected; its chart guidance (direct labels, text table fallback, no pies) was used.

The llm-council reviewed the plan before implementation and changed it:
- Plain-language labels: rules result, reviewer-recorded urgency, queue position.
- "Target", not "escalation".
- Acknowledge only inside the case.
- Fixed the clock reset caused by re-triage.
- Governance overrides are counted across all runs, so re-running triage cannot erase them.

## 7. Governance (`/dashboard/governance`)

Each rate shows its numerator, denominator and the server's definition string. A rate is `null`, shown as "No data", when the denominator is 0. Missing data is never shown as zero. Fewer than 30 cases shows a small-sample warning.

| Metric | Definition |
|:---|:---|
| Override rate | Cases with ≥1 override on **any** of their runs ÷ cases with a triage run in the period |
| Review completion | Latest run signed off ÷ cases in period |
| RED acknowledgment | RED **episodes** on any run of the period's cases. An episode is a rules RED or a reviewer raise to RED, lasting until it is acknowledged or signed off. Episodes are counted within or after target, overdue, or pending. `red_cases` = cases with ≥1 episode. Re-running triage neither erases nor duplicates an episode |
| Median time to sign-off | — |
| Insufficient-data share | — |
| Reviewer decisions on AI fields | All time, not period-filtered |

Reviewers are not ranked. The page states that an override rate does not, by itself, show good or poor triage. Every chart is also a table with the same numbers.

## 8. Limits and known gaps (honest)

Statuses are kept separate:
- **implemented:** code exists
- **automated-tested:** the relevant tests pass
- **browser-verified:** rendered interaction was observed
- **clinically reviewed:** a qualified review actually happened
- **operationally ready:** the real services and safeguards exist and are verified

| Area | Status |
|:---|:---|
| **Alert delivery (P0 before real use)** | **Not implemented.** No SMS, push, phone or supervisor-queue delivery, and no scheduler. Overdue is detected only when someone reads the queue or escalation list. Needed: a server-side deadline worker (outside the request path), a delivery provider with stored credentials, idempotent alert records with delivery attempts kept separate from confirmed provider receipts, an escalation owner and rota, and retry/outage handling. The banner says no alert is sent and that it does not replace the facility's usual escalation. Not operationally ready. **Risk increased by this pass (final council):** a RED now holds RED queue priority until sign-off, so a case can sit at RED indefinitely while nobody is notified. The hold is correct, but it makes the missing delivery more consequential, not less. |
| **Rendered browser walkthrough** | **Browser-verified (partial), 2026-10-04 final pass.** Production build (`next build` + `next start`), headless Google Chrome 154 driven by Playwright from a scratch virtualenv (not a repo dependency), synthetic data, facility mapping on.<br>• **Observed and passing** (Agent D, plus coordinator re-checks): queue order, filters, `aria-current` selection; facility line; RED banner wording; countdown surviving reload; rules vs reviewer panels; hypothetical panel not changing the case; sign-off, override and correction dialogs (initial focus, Escape, focus return, focused error summary with `aria-invalid`); stale correction, override and intake messages; keyboard focus visible on the dark shell and the dark-red button; governance small-sample and episode text; MO redirected from governance; 390 px with no horizontal scroll and "Back to queue"; no 5xx or page errors.<br>• **Unverified:** voice-disagreement rendering (voice off on the demo server); the client-only "Past target" state; governance "No data"; real screen readers; real phones; headed or other browsers. |
| **Override and correction reason codes (P1)** | Implemented and automated-tested, with server validation. **Not clinically reviewed**: prototype lists pending qualified clinical governance. |
| **Reviewer identity (P1)** | Implemented and automated-tested. Identity comes only from the server-checked JWT. `X-SEHAT-*` headers can only cause a 403; extra body fields such as `reviewer_id` are rejected; the proxy forwards only the cookie's token; `actor_id` is never served. Demo accounts are shared and pseudonymous, so a review is attributable to an **account**, not a named clinician. Real named accounts are needed before real use. No names are fabricated. |
| **Facility isolation (security)** | **Implemented and automated-tested as account-level scope; BLOCKED for real use.**<br>• **Configuration:** server-side `ACCOUNT_FACILITIES` (e.g. `anm_demo=PHC-A;mo_demo=PHC-A;supervisor_demo=*`), parsed strictly. A malformed value refuses to start the server and the value is never echoed. Unset means isolation is **off**, the previous behaviour, and the UI shows a plain warning.<br>• **Trust:** scope comes only from server config, looked up by the token's username, which must match the token subject (else 401). It is never taken from claims, headers, query or body. An unmapped account gets an empty scope.<br>• **Enforced in:** `load_case` (an out-of-scope case is the same 404 as a missing one; checked across 18 endpoints), case creation (403, nothing written), queue, escalations and governance (SQL-filtered, including AI review counts), and `GET /audit/{case_id}`. That last endpoint previously skipped case access entirely and returned 200 for a nonexistent case; it now returns 404 even with isolation off.<br>• **Not covered:** `POST /audit/verify` stays global (no case content).<br>• **Limits:** accounts are shared demo accounts, so scope is per **account**, not per person. A case's `facility_code` is still what its creator typed, now limited to the creator's scope. There is no audit event for a facility-denied create; role denials are not audited either. Real use needs a real identity provider with per-person facility membership. Tests: `tests/review/test_facility.py` (21). |
| **Per-field provenance (P2)** | Partial. Only fields changed by a reviewer correction have a recorded source (§3a). Intake-time provenance is not captured; doing so needs a tagged intake payload validated against the voice/OCR/AI records the server holds. |
| **Correction race (P2)** | Fixed and automated-tested (§3a). |
| **Voice disagreement (P2)** | See §10: conflicting voice readings are shown from the server's `conflicts`, with no value chosen. |
| **Live updates** | Polling every 15 s with abort, timeout and latest-response-wins; WebSocket not built (docs/09 8.7). |
| **Component/browser tests** | None (no React Testing Library or Playwright). UI behaviour is tested through pure helpers and the HTTP path. |

The audio alert and Notification API (docs/09 8.5) were deliberately not built: they would suggest an alerting guarantee the prototype does not have.

## 9. Verification (2026-10-04)

**Reviews:**
- Agent D (safety and accessibility) found no P0. Its P1 (re-runs closing or resetting RED) is fixed, and so are its P2s: polling race and timeout, one live region, a white focus ring on dark red, focus moved to the result after an action, correction dialog remounted per run, governance page gated to supervisor/admin, landmarks.
- Agent E (QA) confirmed the API contract live and found no P0 or P1.

**Commands and results** (final hardening pass, final tree, 2026-10-04):

| Check | Command | Result |
|:---|:---|:---|
| Backend tests | `../.venv/bin/python -m pytest -q -p no:warnings` (from `backend/`) | 1075 passed, 10 skipped, 5 xfailed. Baseline at the start of the pass: 1017 / 10 / 5. The skip and xfail sets are unchanged |
| New this pass | `tests/review/test_facility.py` (21), `test_retriage_concurrency.py` (11), `test_red_lifecycle.py` (26, from the independent safety review) | all pass |
| Frontend | `npx tsc --noEmit`, `npm run lint`, `npm run build` | clean |
| Frontend unit tests | `npm test` | 67 passed (pure helpers, mocked data) |
| HTTP-path e2e (Next.js dev proxy + FastAPI, scratch DB, fake AI provider, `ACCOUNT_FACILITIES` set) | `scripts/e2e_review_ui_check.py`, `scripts/e2e_intake_ui_check.py` | 30/30 and 19/19 |
| Rendered browser | Playwright + system Chrome, production build | see §8 (partial; listed items observed) |

**Remaining manual checks** (beyond the automated browser walkthrough in §8; ≈15 min with a headed browser and a screen reader):
1. Log in as `mo_demo`. Seed a RED case as `anm_demo` (SpO2 84).
2. Watch the banner countdown. Reload: it must not reset. Leave it past 3 min: "Past target", then "OVERDUE" after the next refresh.
3. Tab through the queue, the filters and the actions. Open the sign-off and override dialogs and check that focus starts on Cancel, Escape closes the dialog, and focus returns to the opener.
4. Correct vitals: the reason is required. A new run appears, corrected rows say who changed them and from what, and the old run is shown as superseded. Submit the same correction from a second tab and expect the stale message. Submit with no reason and with "other" plus a short text, and check that field errors are announced and focus moves to the error summary.
4a. Override from a stale tab after another tab raised the case to RED: expect the "urgency changed" message.
4b. Seed a voice case with two different confirmed SpO2 readings: the evidence panel shows both, with no value chosen.
5. Log in as `supervisor_demo`: governance and the read-only case view.
6. Check the layout at 1440, 1024 and 390 px.

## 10. Hardening pass (2026-10-04): defects and fixes

Reviews in this pass:
- Agents A (safety), B (backend security), C (frontend; implemented the UI changes) and D (QA).
- An llm-council before implementation: five advisors; the lead synthesised. Adopted: corrections as linked runs rather than a second table, real concurrent tests, a correction diff in history, plainer wording. Rejected, with reasons in the session report: moving the rules engine out of the write transaction, allowing no-change corrections, and gating corrections on facility scoping (any MO can already re-run triage via docs/06).
- An llm-council after implementation.

| # | Defect | Root cause | Fix | Tests | Status |
|:---|:---|:---|:---|:---|:---|
| 1 | Correction race; no correction reason stored | The UI read the latest run, then called the expected-run-free intake endpoint | `POST /triage/{id}/corrections`: expected-run check inside `BEGIN IMMEDIATE`, one correction per run (partial unique index), required reason, identifier check, audit | `test_corrections.py` (22, including a real two-connection race) and e2e 22–25 | Fixed, automated-tested |
| 2 | A later override lowering a reviewer-raised RED closed the escalation silently | The anchor depended on the current priority | The first raise opens the escalation; only ack or sign-off closes it; earlier runs too | `test_lowering_after_a_reviewer_raise_…`, `test_reviewer_raised_red_survives_…` | Fixed, automated-tested |
| 3 | An open RED could sit at YELLOW/GREEN priority | Priority came only from the latest run | An open escalation keeps RED priority (`open_red_escalation`) | `test_carried_red_keeps_red_priority…` | Fixed, automated-tested |
| 4 | Overdue audit event repeated after re-runs | Dedup keyed on the latest run id | Dedup keyed on case + server deadline | `test_one_overdue_audit_event_per_escalation…` | Fixed, automated-tested |
| 5 | Governance lost RED history after re-runs | Counted only the latest run's escalation | `escalation_episodes()` over all runs | `test_governance_keeps_red_history_after_rerun` | Fixed, automated-tested |
| 6 | Stale-view override could undo another reviewer's change | No concurrency token on override | Optional `expected_urgency` → `409 URGENCY_CHANGED`; the UI sends it | `test_override_with_stale_expected_urgency…` | Fixed, automated-tested |
| 7 | Proxy forwarded `..` path segments | `encodeURIComponent("..")` is unchanged | Proxy returns 404 for `.`/`..` segments | e2e 26 | Fixed, automated-tested (HTTP) |
| 8 | Missing tests for malformed override payloads and identity spoofing | — | Parametrised validation tests, spoof tests (`X-SEHAT-User-ID`/`Role`, `reviewer_id`/`reviewer_name` body fields), and a check that the audit actor is the token's | `test_override_malformed…`, `test_reviewer_identity…` | Automated-tested |
| 9 | Polling: a hung request was never aborted | `Promise.race` timeout only | `createRequestGate`: abort on supersede, path change, unmount and 20 s timeout; latest wins; no retries | `requestGate.test.ts` (7) | Fixed, unit-tested; not browser-verified |
| 10 | Override errors not tied to fields | Only a summary list existed | `formErrors.ts`: server `details.errors[].field` → control, `aria-invalid`/`aria-describedby`, focused summary | `formErrors.test.ts` (6) | Fixed, unit-tested; not browser-verified |
| 11 | Voice disagreement not shown | The panel read only `values` | Shows `conflicts` (each reading with its source, none chosen) and `unresolved` | none (no component test stack) | Implemented; not tested in UI |
| 12 | "0 missing" pill after consent withdrawal | `missing_fields` blanked, determination kept | "Needs information (details hidden)" | `review.test.ts` | Fixed, unit-tested |
| 13 | Per-field provenance | Intake stores one combined input | Corrected fields only (§3a); the rest says "not stored" | `test_provenance_and_history…` | Partial (by design) |
| 14 | Raise → acknowledge → lower → raise again on one run was reported as already acknowledged (final council, Contrarian) | Escalation state took the first raise and the first acknowledgment | One shared episode walk; the new raise is a new escalation; one-ack-per-run index dropped | `test_a_new_raise_after_acknowledgment_opens_a_new_escalation` | Fixed, automated-tested |
| 15 | A reflexive acknowledgment lowered a carried or raised RED's queue priority (final council, First Principles) | Priority relief was tied to acknowledgment | RED priority holds until sign-off | `test_carried_red_keeps_red_priority_until_signed_off`, `test_lowering_after_a_reviewer_raise_…` | Fixed, automated-tested |
| — | Alert delivery, facility scoping, browser walkthrough, clinical approval of reason codes | see §8 | not fixed | — | **Blocked** |

**Migration 9** (`TRIAGE_CORRECTION_STATEMENTS`): three nullable columns on `triage_runs` (`corrects_run_id`, `correction_reason_code`, `correction_reason_text`), a partial unique index (one correction per run), and `DROP INDEX idx_review_events_one_ack` (§5). It is additive, rewrites no existing rows, and keeps the append-only triggers. `tests/privacy/test_migrations.py` now expects version 9.

Rollback: older code ignores the columns, so running the previous build against a v9 database works. To undo the indexes: `DROP INDEX idx_triage_runs_one_correction`. Recreating `idx_review_events_one_ack` fails if any run already has two acknowledgments; that is expected after a re-raise. `ALTER TABLE … DROP COLUMN` is possible on SQLite ≥3.35, but it rewrites the append-only table without firing its triggers and would discard correction history (`corrects_run_id` also needs its index dropped first). This is not recommended; leave the columns nullable.
- **Upgrade risk:** each migration step runs `PRAGMA foreign_key_check` over the whole database. A v8 file holding a dangling foreign key (possible only if rows were written with foreign keys off) fails step 9, and the server will not start. Run `PRAGMA foreign_key_check` on a copy before upgrading. Agent A verified a v8 → v9 upgrade on a populated database: rows kept, triggers effective, older 8-step code a no-op on v9 (`tests/review/test_red_lifecycle.py::test_migration_9_on_populated_v8`).

### Final hardening pass (2026-10-04)

Reviews:
- Agents A (safety), B (backend security; implemented facility isolation), C (frontend; implemented the UI) and D (QA; first rendered walkthrough).
- An llm-council before implementation and after.
- Council decisions:
  - **Dropped** a proposed server-side overdue sweep. It would only write a "system noticed" audit row and deliver nothing, which risks implying the alert gap is handled (First Principles).
  - **Isolation labelled account-level, not per-person** (Contrarian).
  - **Recorded disagreement:** First Principles wanted isolation to fail closed when unconfigured. It stays off for the dev demo, with a persistent plain-language warning; the e2e tests and the walkthrough run with a mapping.
  - **Rejected:** a "perturbations tried" caption on the what-if panel. The API returns only changes that alter the result; showing tried probes would need a new contract.

| # | Defect | Root cause | Fix | Tests | Status |
|:---|:---|:---|:---|:---|:---|
| F1 | Any MO or supervisor could read every facility's cases | No facility on identity | `ACCOUNT_FACILITIES` server-side scope; enforced in `load_case`, create, queue, escalations, governance, audit read | `test_facility.py` (21; 18 endpoints) and e2e with the mapping on | Implemented, automated-tested; **real use blocked** (shared demo accounts) |
| F2 | A token could swap in another username and borrow its scope | Username not bound to the subject | `_decode` requires `demo_user_id(username) == sub` | `test_facility.py` | Fixed, automated-tested |
| F3 | `GET /audit/{case_id}` skipped case access (200 for a nonexistent case) | Direct SQL without `load_case` | `load_case(..., "read")` | `test_facility.py` | Fixed, automated-tested |
| F4 | Intake re-triage could supersede a newer run from a stale screen | No expected-run token | `expected_run_id`, required once a run exists, checked inside `BEGIN IMMEDIATE` | `test_retriage_concurrency.py` (11; real two-connection races), e2e 15a/15b, browser intake stale message | Fixed, automated-tested, browser-verified |
| F5 | Acknowledge, then a lower re-run, dropped RED priority without sign-off (Agent A V1) | Only *open* episodes carried | `red_hold()`: any RED since the last sign-off holds RED priority | `test_red_lifecycle.py` | Fixed, automated-tested |
| F6 | Supervisor bar said "no facility" for an all-facilities scope | `null` treated as `[]` | `facilityNotice` maps enforced + null to all facilities | `facilityScope.test.ts`; browser re-check | Fixed, browser-verified |
| F7 | Focus fell to `<body>` after a 409 in dialogs | The submit button was disabled while busy; the summary rendered nothing for conflicts | `FailureNotice` takes focus | Browser re-check (activeElement = notice) | Fixed, browser-verified |
| F8 | A duplicate "Fix before recording" summary appeared after a conflict | `touched` not reset | Reset on conflict | Browser re-check | Fixed, browser-verified |
| F9 | Unknown `?case=` showed a generic error with a pointless retry | No 404/400 state | "Case not found" (same for out-of-facility) without retry | Browser re-check | Fixed, browser-verified |
| F10 | Patient token wrapped mid-token in the queue | `break-all` | No-wrap token; the pill wraps instead | Browser re-check | Fixed, browser-verified |
| F11 | Migration rollback text claimed `DROP COLUMN` is impossible | Inaccurate | Corrected, plus a foreign-key-check upgrade risk note | `test_migration_9_on_populated_v8` | Docs fixed |
| — | Alert delivery, real identity and per-person facility membership, clinical approval of reason codes, real screen reader and device checks | — | not fixed | — | **Blocked / pending** |

