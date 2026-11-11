# Summary note from every recorded source: findings and plan

> 2026-11-11. Base: `origin/main` `0b5b23b`. **Status: owner approved D1–D4 as recommended on 2026-11-11. S1–S5 implemented on branch `feat/summary-note-all-sources` (uncommitted); S6 partly done. See §10.**
> Research prototype, non-diagnostic. The rules engine stays the only urgency authority. A named human signs off.

## 1. Question asked

Is the summary note generated, and does it carry everything collected about the patient: reports, voice, X-rays, handwritten notes, body map, questioning, and missing information?

## 2. Answer in short

The summary **is generated** and works. It is the encounter summary of `docs/19` (`backend/app/ai/encounter_summary.py`, shown by `frontend/src/components/review/EncounterSummaryPanel.tsx`).

It does **not** yet carry everything. Two collected sources never reach it, one is only partly covered, and the result is laid out as a review panel, not as one readable clinical note.

## 3. How this was checked

- **Code:** read the summary module, its five source readers, the staleness inventory, the image service, the interview package and the reviewer panel.
- **Live probe (scratch database, fake AI provider, replayed OCR, fake image backend):** one case with typed intake, a lab report with two rows confirmed, a chest X-ray, a body map, a triage run and three interview answers; then `POST /cases/{id}/encounter-summary`.
  - Result: `201`, 12 written sentences, source types `ai_field`, `ocr`, `triage_form`, `body_map`.
  - The X-ray (status `described`, with findings) and all three interview answers were absent from the summary JSON.
  - "Severity" was still listed as missing although the interview had recorded it.
- **Not checked:** the screen in a browser. The Claude in Chrome extension was not connected, and ports 3000 and 8000 hold the owner's own running servers, which were left alone. Real OCR engines, real speech and a real image model were not run.

## 4. Coverage today

| What is collected | In the summary today? | Evidence |
|---|---|---|
| Typed intake and voice transcripts (symptoms, duration, medicines, red-flag mentions) | Yes, every extraction run, with review state | `_ai_source`; probe |
| Spoken values confirmed on read-back | Yes | `_voice_source` |
| Lab reports (printed) | Yes: checked rows in text, unchecked rows in tables only | `_ocr_source`; probe |
| Prescriptions, including handwriting | Yes, as medicine rows; uncertain handwriting flagged, never in text | `_ocr_source` |
| Triage vitals, age, pregnancy, red-flag screen | Yes | `_triage_source`; probe |
| Body map | Yes | `_body_map_source`; probe |
| Missing information and follow-up questions | Yes, but it ignores interview answers | `missing_information`; probe |
| **X-ray, ECG, wound and skin images** | **No** | Not in `SOURCES`; not in `_INVENTORY` |
| **Questioning (interview answers)** | **No** | Not in `SOURCES`; not in `_INVENTORY` |
| **Handwritten free-text notes** (not a lab table or a prescription line) | **No**: documents yield only lab and medicine rows | `_ocr_source` handles `lab` and `medication` kinds only |

### Gaps

| # | Gap | Effect |
|---|---|---|
| G1 | Medical images are not a summary source | An uploaded X-ray and its described findings never appear. A flagged image is not listed under "Needs attention". |
| G2 | Images are not in the staleness inventory | Uploading, acknowledging or deleting an image does not mark an existing summary out of date. |
| G3 | Interview answers are not a source, and not in the inventory | Answers the health worker confirmed are invisible to the medical officer in the summary. Planned as V4 of the interviewer plan, not built. |
| G4 | Missing information ignores interview answers | A question answered in the interview is still listed as missing. |
| G5 | No patient and visit header | The note shows age and pregnancy only. No scenario label, facility, visit date or patient code. |
| G6 | Handwritten free-text notes are not read into the summary | Only table rows and prescription lines are captured. (The narrative OCR from Phase A was deleted on 2026-11-07.) |
| G7 | Layout is a review panel | Sentences are a bullet list among tables. There is no single note in a clinical order, and no copy or print. |
| G8 | The summary is built only on a button press | A reviewer who does not press "Build summary" sees none. |
| G9 | The interview has no screen | Backend V1 is merged; the step 6 page still uses the older device-only answers. Tracked in `scripted-voice-interviewer.md` (V3), out of scope here. |

## 5. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | How the note text is written: **(a)** keep template sentences, no model call; **(b)** add an optional model rewrite beside it, behind the same grounding gate | **(a)**. It matches the recorded decision of 2026-10-10 and the evidence in docs/19 §7. A model writer brings back the hallucination risk the gate cannot fully remove. The note can still read like a sectioned clinical note (§6 S4). |
| D2 | X-ray findings in the note: **(a)** the described fields in an "Imaging" table with the disclaimer, and one fixed sentence in the text that names the image and its review state but no finding; **(b)** finding text in the prose too | **(a)**. Image findings are model output that no rule validates. Keeping them in a labelled table with the image link follows docs/18 and Agent Rule 2. |
| D3 | Handwritten free-text notes: **(a)** later, as its own phase with its own review step; **(b)** in this work | **(a)**. It needs a new OCR path, per-line human checks and its own evaluation. Mixing it in would delay G1–G5. |
| D4 | Where to work: a new branch `feat/summary-note-all-sources` in a worktree under `.worktrees/`, uncommitted until the owner says "commit this" | As stated, following earlier phases. |

## 6. Plan (tests written before the code in every step)

### S1. Medical images as a source (G1, G2)

- New reader `_image_source` in `encounter_summary.py`, reading `medical_images`, acknowledgements and purge events through `image_service.row_view`.
- New section `imaging`. One fact per image: image type, status, structured fields, confidence band, flags, acknowledged or not, link to the stored image.
- New provenances in the single registry: `image_described_unacknowledged` (trust `unchecked`, tables only) and `image_described_acknowledged` (trust `checked`).
- Text (per D2a): "Chest X-ray on file: AI visual description available, reviewed by a clinician / not yet reviewed (see Imaging)." No finding text, no number.
- "Needs attention": an image whose flag is not acknowledged; an image that failed or was not described.
- Purged images are not shown. Without `ai_assist` consent the described text is excluded and listed under `excluded_sources`, as AI fields are.
- Inventory: add `medical_images` (with status), `medical_image_acknowledgements`, `medical_image_purge_events`.
- The summary still never writes triage or review rows; the existing source-scan test is extended.

### S2. Interview answers as a source (G3, G4)

- New reader `_interview_source` using the latest event per question.
- New section `interview`: question, answer, who answered, typed or from a transcript span, recorded by which role. **Table only, never prose** (owner decision I4).
- "Not sure" and "not checked yet" stay missing (interviewer safety rule 5).
- Missing information: a patient question with a confirmed, known answer is no longer listed as missing; the row points to the interview table.
- Inventory: add `interview_answer_events`.
- Supervisor keeps metadata only. A withdrawn consent hides answers, as it does for transcripts.

### S3. Patient and visit header (G5)

- New block `header`: scenario, facility code, visit start time, age, pregnancy state, linked patient code when one exists.
- No name, phone or address: the summary stays free of direct identifiers.

### S4. One sectioned note (G7)

- New field `note_sections`, built from the same gated sentences and rows, in a fixed clinical order:
  1. Patient and visit
  2. Urgency (rules engine) and sign-off state
  3. Presenting problem
  4. History: duration, symptoms reported and denied, body map
  5. Interview answers
  6. Measurements
  7. Lab values
  8. Imaging
  9. Medicines
  10. Red-flag screen and mentions
  11. Missing information
  12. Waiting for a check, conflicts, withheld items
  13. Sources, trust legend, disclaimer
- Empty sections are left out. Each line keeps its fact ids, so every statement still opens its source.
- A plain-text rendering of the same sections for copy and print. It carries the disclaimer and the "draft, pending review" state.
- Schema version becomes `.4`. Older snapshots show as out of date until refreshed, as today.

### S5. Reviewer screen (G7, G8)

- The note is shown first as one document, then the existing attention list, evidence status and source tables.
- New "Imaging" and "Interview answers" sections with trust pills and source links.
- "Copy note" and "Print" buttons.
- When a reviewer opens a case that has no summary, it is built once automatically. "Refresh" stays manual for an out-of-date one.
- View logic goes in `src/lib/encounterSummary.ts` with `node --test` cases; `tsc` and ESLint clean.

### S6. Evaluation and documents

- Add image and interview cases to `tests/ai/fixtures/encounter_cases.json`; re-run `scripts/eval_encounter_summary.py` and report passed / total.
- Update `docs/19`, `docs/06` (contract) and `docs/18` §8; raise the CI skip-gate minimum.

### S7. Verification before anything is called done

- Full backend suite and frontend tests, types and lint.
- `sehat-real-app-check` on a scratch database, extended with an X-ray and an interview answer, with screenshots looked at.
- A browser pass of the medical officer's screen (Claude in Chrome once connected, otherwise the built-in browser).

## 7. Not changed by this plan

- Urgency rules, the rules engine and sign-off.
- Consent rules and the audit log's "ids and enums only" rule.
- The unregistered WIP routers (`cds`, `patient`, `referrals`, `surveillance`) and `frontend/package.json`.

## 8. Technology note (mentor suggestion: Mistral, Qdrant, Cloudflare)

- The note is built from this case's stored records, so it needs **no vector database**. Qdrant would only matter for a later protocol-lookup feature.
- The extraction model sits behind the existing provider adapter (`fake`, Azure by environment, local model). Staying on **Azure OpenAI** fits the project's stated Cognizant and Semantic Kernel alignment. A Mistral model can be added as another adapter later without touching the note.
- Hosting on Cloudflare is a deployment question and does not affect this work.

## 9. Limits

- The probe used a fake AI provider, replayed OCR and a fake image backend. It shows what the summary includes, not how accurate any reading is.
- G6 and G9 are from reading the code, not from a run.
- Layout is in the manner of sectioned clinical notes. No claim is made of matching any commercial product's accuracy.

## 10. Implementation record (2026-11-11)

| Step | State | Evidence |
|---|---|---|
| S1 images | Done | `_image_source`, three image provenances, attention kind `image_flag_unacknowledged`, inventory rows; 4 tests |
| S2 interview | Done | `_interview_source`, `interview_confirmed` (table only), answered questions leave the missing list; 2 tests |
| S3 header | Done, with one change | `header` has scenario, facility, visit start and `patient_record_linked`. The patient code itself is **not** stored in the summary, to keep it free of identifiers |
| S4 note | Done | `note_sections`, `note_text`, schema `.4`; a test checks that every fact appears once |
| S5 screen | Done | Note shown first, Copy note, Print, Imaging and Interview sections, built once automatically for a reviewer |
| S6 evaluation and docs | Partly | `docs/19` §10 and `docs/06` updated. **Not done:** new cases in `encounter_cases.json`, the evaluation re-run, the CI skip-gate minimum, `docs/18` §8 |
| S7 verification | Partly | Backend 1596 passed, 14 skipped, 5 xfailed. Frontend 134 passed; `tsc` clean; ESLint 0 errors (3 earlier warnings). Chrome on a scratch instance: note built on open, Imaging and Interview shown, Copy note worked, no console errors. **Not done:** Print dialog, `sehat-real-app-check` with real OCR, a flagged image in the browser |

## 11. Follow-up work completed (2026-11-11, later the same day)

Record: [`../implementation/remaining-work-completion-report.md`](../implementation/remaining-work-completion-report.md).

- Interview screen on the Questions step (closes G9), print rework, evaluation corpus with image and interview cases, CI skip gate, `docs/18` §8.
- Real OCR, the real local image model and real speech were run on synthetic inputs, as component tests and through the running app.
- Browser checks: 30 / 30 in fake mode and 29 / 29 in real mode.
- Still open: the native print dialog, GitHub Actions for the new gate, handwritten free-text notes (D3).
