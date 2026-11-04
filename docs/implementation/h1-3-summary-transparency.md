# H1-3: Summary transparency (doctor-facing encounter summary)

> Implemented 2026-11-04 on branch `fix/p0-1-lab-parser`, after P0-2 (`530b03c`). **Uncommitted, awaiting approval.**
> This is a research prototype. It is non-diagnostic, the summary stays template-only, the gate is unchanged, and urgency is unchanged.

## 1. What changed

### Backend

`backend/app/ai/encounter_summary.py`, schema `encounter-summary-2026-11-04.2`. The schema version is part of the inventory hash, so every older snapshot becomes stale and is rebuilt on refresh. No migration is needed. The new fields are optional, so older clients ignore them.

| Field | What it holds | Source of truth |
|---|---|---|
| `unmatched_documents` | Completed documents whose "Is this the patient's report?" answer is missing, no, or not sure. Each row has its label, machine-read value, printed flag, page and regions, review status and OCR agreement. | `_ocr_source`. The rows are **not facts**: they never reach the narrative, the gate, or `facts`. |
| Fact flags `ocr_engines_agree`, `ocr_engines_disagree`, `ocr_single_reading` | The OCR engines' cross-check for each document value | `ocr_agreement(checks)` reads the `second_engine_agreement` check. Trust and provenance are unchanged, so agreement is **never** a human check. |
| `withheld` | What the written summary leaves out, and why (see the reasons below) | `withheld_items()` mirrors `render_narrative`'s own rules and copies the gate's blocked reasons. A blocked sentence's text is never stored. |
| `evidence_status` | Counts of documents, document observations and summary contents, plus a readiness state and reasons | Counted while reading the same persisted rows. Document counts are `null` when the documents source failed. No confidence score. |

The `withheld` reasons:

- `source_not_verified`: a document value that is still unchecked (`document_unchecked` is never written into the text).
- `evidence_conflict`: a conflicting vital, lab or duration.
- `document_not_matched`: a document not matched to the patient.
- The gate's own codes, such as `diagnostic_language` and `number_not_in_cited_facts`.

How readiness is decided:

- The state is `incomplete` when any of these apply: `source_unavailable`, `document_processing_pending`, `processing_failures`, `document_matching_pending`, `verification_pending`, `no_triage_run`, `triage_predates_evidence`.
- Otherwise the state is `ready_for_clinician_review`. The page says this is neither an approval nor clinical completeness.
- Clinician acceptance is still the separate review event.

### Frontend

`components/review/EncounterSummaryPanel.tsx` and `lib/encounterSummary.ts` add:

- **Evidence status:** the readiness state and its reasons, a compact count grid, and one line that names the three separate states (OCR engines agree · checked by a person · clinician review of this summary).
- **Withheld pending verification:** grouped by reason and section. Each group shows plain-language reason words and its values, with each value linked to its row on the page.
- **Documents awaiting patient matching:** the rows, labelled "Unmatched", with OCR agreement and page. The panel says the values are not part of the summary and that nothing is matched automatically.
- **Per-value OCR state:** "Machine-agreed — not yet checked", "Machine readings differ" or "One machine reading only". These are shown apart from the trust pill.
- **Verification links:** "Open in verification" and "Answer 'Is this the patient's report?'" links appear only for roles that can open `/intake/documents` (`canOpenVerification`).
  - The medical officer's dashboard cannot open that page (`lib/auth.ts` ROUTE_ACCESS: `/intake` is open to patients and ANMs).
  - So the medical officer sees a note instead: "Checked on the documents step by the health worker who created this case".
  - The access policy was not changed.
- **Deep links on the documents page:** `&field=<id>` opens that row, and `&doc=<id>` scrolls to its patient question.

## 2. Verification

**Tests:**

| Suite | Result |
|---|---|
| Backend, full | **1467 passed**, 2 failed (the same 2 baseline failures, from the uncommitted work-in-progress routers) |
| Backend, new | 10 transparency tests in `tests/ai/test_encounter_summary_transparency.py`; the encounter-summary GET and POST added to the cross-facility endpoint list in `test_facility.py` |
| Frontend | **113/113** (7 new); `tsc` and ESLint clean; `git diff --check` clean |

**Real app** (real PaddleOCR and Surya, scratch database, headless Chrome): `scripts/e2e_real_app_browser_check.py` passed 12/12. Results before and after matching the sample report:

| Check | Before matching | After matching and confirming urea |
|---|---|---|
| Lab facts in the summary | 0 | Only urea is written into the text |
| Unmatched rows listed | 13 | — |
| Withheld reason | `document_not_matched` | `source_not_verified` |
| Readiness | Incomplete (matching) | Incomplete (verification) |
| OCR states | — | 5 "Machine-agreed — not yet checked" labels, matching the count; 5 engine disagreements still visible |

On the medical officer's dashboard there are 0 verification links and the note is shown. The ANM deep link opened SERUM CREATININE.

**Found and fixed during the run:**

- The first version linked the medical officer into ANM-only pages, which redirected to `/dashboard/overview`.
- The withheld list showed 12 separate cards for one reason.

## 3. Limits

- **The doctor's screen cannot open the verification workflow.** Document checking happens on the ANM's documents step. Letting medical officers verify from their dashboard needs a product and access decision, then a doctor-side review view.
- **Only this visit is shown.** "Historical" means earlier entries within this case; earlier visits are not linked yet (P1 patient registry).
- **Medical images and AI-extracted values are not counted.** The counts cover document observations only, and medical images are still not a summary source (RC5).
- **Accept stays enabled for an incomplete summary.** It is still offered when the summary status is complete but readiness is incomplete, because tying them together would change the acceptance policy. Readiness is shown above the button.
- **Two OCR footer fragments still appear as rows** on the sample report.
