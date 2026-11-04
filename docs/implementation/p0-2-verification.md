# P0-2 — "Check these first" verification (documents screen)

> Implemented 2026-11-04 on branch `fix/p0-1-lab-parser`, after P0-1 (`b4f60b7`). The plan is
> `SEHAT_AI_Architecture_Audit_and_Plan.pdf` rev. 2, tasks H1-1 and H1-2. Research prototype; non-diagnostic.

## 1. What it does

`GET /cases/{id}/verification-queue` (`backend/app/verification_queue.py`) lists one case's unchecked evidence in checking order. It is **read-only**: it confirms, changes and records nothing, and never writes triage runs, review events or audit rows. Every item carries reason codes saying why it is where it is. The queue uses the same access as the encounter summary: the creator ANM or a medical officer, with `triage` consent in effect.

| Order | Priority | When |
|---|---|---|
| 1 | `match_document` | A completed document whose "Is this the patient's report?" question is unanswered, or answered no or not sure. Its rows cannot be confirmed until it is answered. |
| 2 | `could_change_urgency` | A spoken vital that has not been read back, where substituting it into the **latest triage run's stored input** makes the rules engine give a different urgency. The result is shown as hypothetical: "If this value is right and is entered on the triage form, the rules would give RED (now YELLOW). Hypothetical — not recorded." |
| 3 | `abnormal_lab` | A machine-read lab row with a printed H, L or abnormal flag, or a value outside the printed or sourced range. The rules engine never reads labs, so this second ranking stops lab values sinking to the bottom of the list. |
| 4 | `routine` | Everything else, in document and recording order. |

How the queue treats unclear cases:
- When there is no triage run, nothing is claimed: the item has the reason `no_triage_run_to_compare`.
- A spoken value that cannot be read gets `needs_correction` and no hypothetical urgency.
- Rows marked "not sure" stay in the queue.

## 2. Documents screen

`frontend/src/app/intake/documents/page.tsx`, `components/review/CheckFirstPanel.tsx` and `lib/verificationQueue.ts` add:

- **A "What to check, in order" panel** above the documents. Clicking an item opens that row, or scrolls to the patient-match question. A spoken value links to the voice step.
- **Auto-advance:** after a recorded decision, the next row in queue order opens. After an error, the screen stays on the same row.
- **Keyboard shortcuts:** <kbd>C</kbd> confirm, <kbd>U</kbd> not sure, <kbd>R</kbd> wrong / not on report.
  - The shortcuts follow the same rules as the buttons: Confirm stays off when the buttons would be off.
  - Enter is not used, because Enter already presses whichever button has focus.
  - The keys are ignored while typing in a box, and when Ctrl, Cmd or Alt is held.
- **Per-row reset:** the review panel now resets for each row (`key=field_id`), so a correction half-typed on one row is never carried into the next.
- **Older backends:** if the queue endpoint is unavailable (404), the page works exactly as before.

## 3. Defect found by the real-app check, and fixed

On the real sample kidney-function report, Surya prints the flag **before** the value ("L 17"). Verification only removed a flag printed *after* the value, so every flagged row was "disputed" and could not be confirmed. P0-1 caused this to show up: before P0-1 those rows never matched across the two engines.

`verify._cell_value_text` now removes the row's own flag at either end of the second reading. Only the flag already read for that row is removed, so a flag the first reading missed still shows as a disagreement. The fix applies to both the table-row and the cell comparison. A test covers both the `L 17` vs `17` agreement and the `L 0.96` vs `0.69` disagreement.

## 4. Verification

**Tests:**

| Suite | Result |
|---|---|
| Backend, full | 1454 passed, 2 failed (the same 2 as the Phase 0 baseline; caused by the uncommitted work-in-progress routers) |
| Backend, new | 9 queue tests (pure ranking, API order, read-only check, access control, spoken SpO2 85 ranked first using the engine's own result) and 1 verify test |
| Frontend | 106/106 (6 new); `tsc --noEmit` and ESLint clean |

**Real app** (scratch database, real PaddleOCR and Surya, Next.js dev server, headless Chrome):

1. Uploaded `public/med_img/test imag.jpeg`.
2. The queue listed: match the document → SERUM UREA (L) → SERUM CREATININE (L) → routine rows.
3. Answered "yes, this patient's report".
4. Pressed **C** on urea: the queue went from 13 to 12 and the panel opened SERUM CREATININE.
5. Typing "cur" in a correction box did nothing.
6. The encounter summary's written text then read: "Document value, reviewed: SERUM UREA 17 mg/dL (printed flag: L)."
7. No browser console errors.

**Intermittent test failure.** `test_no_document_text_in_logs_or_audit_and_no_triage_written` failed once in one full run. It passed in 3 later full runs, 20 isolated runs and 2 large subset runs. The cause has not been found, so this failure is unexplained, not fixed.

## 5. Limits and next steps

- **Display-only spoken values are not queued.** Symptom duration is a display-only value and is not in the queue. The voice item is a link to the voice step; read-back still happens there.
- **The spoken-vitals ranking needs a triage run.** It compares against the latest stored triage input only, so with no triage run, spoken vitals are listed as routine.
- **Some real-OCR noise remains.** Two one-word footer fragments ("idn e ( e", "www.labsmartl") still appear as rows, and a garbled calcium unit ("Ip6") correctly stays disputed.
- **The summary display changes are not done yet** (plan H1-3):
  - labelled rows for documents not yet matched to the patient;
  - a "machine-agreed, not yet checked" label;
  - withheld sentences shown on screen.
- **Speed is unmeasured.** No timed walkthrough with a real reviewer has been done.

## 6. Review before commit (2026-11-04)

| Check | Evidence |
|---|---|
| The queue is read-only | `test_queue_writes_nothing_and_refuses_without_access`: review, attestation, triage and audit row counts are unchanged |
| Order is deterministic | A stable sort over SQL-ordered inputs (`created_at`, `ordinal`); the pure ranking tests check it |
| Unmatched documents come first; labs are ranked; hypothetical urgency is labelled | Pure ranking tests and the API order test. `urgency_if_confirmed.hypothetical` is always `true`, and the UI text says "Hypothetical — not recorded" |
| Engine agreement is not a human check | `test_engines_agreeing_is_not_a_human_check`: an agreed row stays `machine_read` and stays in the queue |
| C/U/R decisions are stored | The keys call the same `decide()` as the buttons, through the same review endpoint. Seen in the real-app run (`ocr_review_events`: SERUM UREA, unsure, anm) |
| Original evidence is kept; corrections are audited | Unchanged: review events are append-only and logged as `ocr_review_resolved` (existing tests) |
| Shortcuts don't fire while typing; the panel resets per row | `keyAction` tests; `key=field_id`. In the real-app run, typing "cur" in a correction box did nothing |
| Access control | Patient: 403. Supervisor: 403. Another facility's case: the same 404 as a missing case (`test_facility.py` endpoint list). No triage consent: refused |
| Flag order | `L 17` = `17 L` = `17`, and `H 17` = `17 H` = `17`. Still disagreements: `L 18` vs `17`; a flag the first reading lacked; a different flag (`test_flag_before_or_after_the_value_compares_as_the_same_value`). A different unit is still a dispute (existing `test_verify.py`); a different analyte never matches (matching is by analyte key) |

Results: backend 1457 passed, with the same 2 baseline failures; frontend 106/106; `tsc` and ESLint clean; `git diff --check` clean.
