# 19 — Clinical Encounter Summary (case-level, source-linked)

> Research prototype, not a clinically validated device. The summary is **not a diagnosis**. Urgency is the rules
> engine's. A named human reviews it. Implemented 2026-10-11 on branch `feat/clinical-encounter-summary`.

## 1. What it does

`POST /cases/{id}/encounter-summary` builds one summary for the whole case from everything recorded:
- typed intake and transcripts (all AI extraction runs, each field with its latest human decision);
- voice read-back decisions, including the spoken symptom duration;
- OCR documents: reviewed rows, rows nobody has checked yet, document type and dates;
- every triage run (the latest is authoritative; earlier entries become history);
- the reviewer's sign-off and override state;
- the body map.

It returns schema-validated JSON plus a sectioned, template-written narrative. Every sentence cites fact ids, and every fact cites its sources. **There is no model call.** The narrative is template-only (user decision, 2026-10-10). This follows the earlier council rule that notes are templates, not model prose (docs/16 §6).

## 2. Root causes it fixes (investigation, 2026-10-10)

| # | Finding | Fix |
|---|---|---|
| R1 | The note used **one extraction run**, so facts from earlier runs, and typed text that was not re-sent, were lost | The summary reads every run of the case |
| R2 | **Triage-form vitals, age and pregnancy** never became facts | Read from `triage_runs.input_json` with `triage_form` provenance |
| R3 | **Voice-confirmed values were ignored**, and the structured spoken duration was display-only | `voice.service.case_resolutions` feeds them in as `voice_confirmed` / `voice_corrected` |
| R4 | **Bug:** a reviewed prescription line rendered as "`<drug> .`", losing strength, dose pattern, frequency and duration | `note.ocr_medication_text`, shared by the note and the summary |
| R5 | Document dates, document type and unchecked or unattested rows were dropped | Documents are read directly. Unchecked rows are shown and tagged. Unattested documents show no values, only a pending item |
| R6 | Duration was one free-text string, not tied to a symptom | `parse_duration` (never computes an onset date). It is linked to a symptom only when exactly one reported symptom shares the sentence |
| R7 | A 500-character cap cut the unreviewed facts first | No cap. Sections are bounded by their facts, and conflicts and pending items lead the page |
| R8 | The note showed rule ids only. Rule reasons, scores, missing fields and reviewer status were absent | Urgency block carries rule reasons and sources, NEWS2/qSOFA, rules missing fields, and sign-off/override |
| R9 | **Bug:** `POST /ai/notes` returned a 500 (`KeyError`) when a keyword red flag was unreviewed | Tag added. Every summary state comes from one exhaustive registry, which a test checks |
| R10 | No screen showed any note | `EncounterSummaryPanel` in the reviewer workspace |
| R11 | **Found by the evaluation corpus:** the spaCy NER labelled "Fever" as `LOCATION`, so the symptom was redacted before extraction ("Fever for three days with cough" lost the fever) | `pii.drop_clinical_terms`: a PERSON/LOCATION span is kept only when its *whole* text is a closed clinical term. Pattern identifiers and the residual sweep are unaffected (§9) |

## 3. Pipeline (`backend/app/ai/encounter_summary.py`)

1. **Assemble** (`assemble_evidence`, public and reusable). Each source is read on its own.
   - If a source fails, it is listed in `unavailable_sources`, the status becomes `partial`, and the summary cannot be accepted.
   - Without `ai_assist` consent, AI fields are left out and listed in `excluded_sources`. This is a consent choice, not a failure.
2. **Normalise and reconcile** into `Fact`s: `{fact_id, section, key, label, display, value, provenance, trust, sources[], flags[]}`.
   - **Merging:** equal values merge, and the merged fact cites every source.
   - **Vitals:** the comparison form is °C, integer BP and rounded temperature; the display keeps the source's own form ("38.9 °C (said as 102 °F)").
   - **Conflicts:** different values are shown as a conflict with **no value chosen**. This covers vitals, the same lab test on the same date, durations, and a symptom that is both reported and denied.
   - **Labs:** one row per test and date. A different date is history, not a conflict. A duplicate document is merged, and its sources are kept.
   - **Medications:** documented on a prescription, or mentioned by the patient. They are never described as taken.
3. **Validate** with `EncounterSummary` (Pydantic, `extra="forbid"`, `SUMMARY_SCHEMA_VERSION`).
4. **Gate every narrative sentence** (`gate`). A failing sentence is listed in `blocked`, never repaired. A sentence must:
   - cite facts that exist;
   - contain only numbers that appear in the cited facts;
   - carry the tag of any unchecked fact it shows;
   - never contain a machine-read document value that nobody has checked;
   - pass the output guard (no diagnostic, prescriptive or identifier-like text).
5. **Render** the triage-handover sections: presenting problem, duration, symptoms reported and denied, measurements, lab values, medicines, red flags, body map, context.
   - Dates and printed ranges stay in the tables, because the identifier backstop blocks full dates in prose.
6. **Persist** an append-only snapshot keyed by `inventory_sha256`.
   - The key is a hash of **every source table's** row count and newest id for the case, plus in-place statuses. A source that the assembler failed to read still changes the hash (council amendment).
   - The same inventory reuses the stored summary. Any change marks it `is_stale`.

### Trust vocabulary (one, shown on the page)

| Trust | Meaning | Provenances |
|---|---|---|
| Checked by a person | Checked against its source | `ai_accepted`, `ai_corrected`, `keyword_accepted`, `voice_confirmed`, `voice_corrected`, `document_confirmed`, `document_corrected` |
| Entered by staff | Typed or marked directly | `triage_form`, `body_map` |
| Not yet checked | Machine-read; do not rely on it | `ai_unchecked`, `keyword_unchecked`, `document_unchecked` (never in prose) |

## 4. Endpoints (reviewer roles: creator ANM or medical officer; `triage` consent required)

| Method | Path | Notes |
|---|---|---|
| POST | `/cases/{id}/encounter-summary` | 201. No body. Reuses the stored summary when the source inventory is unchanged |
| GET | `/cases/{id}/encounter-summary` | Latest summary with `is_stale`, `stale_message` and review state, or `summary: null` |
| GET | `/cases/{id}/encounter-summary/history` | Ids, status and rules urgency of every snapshot |
| POST | `/cases/{id}/encounter-summary/{sid}/review` | `{outcome: accepted\|needs_changes, acknowledged_conflicts, supersedes}`. No free text |

Review errors:

| Code | Meaning |
|---|---|
| `409 SUMMARY_STALE` | Sources changed; refresh |
| `409 SUMMARY_SUPERSEDED` | Not the latest summary |
| `409 REVIEW_CONFLICT` | `supersedes` does not name the latest review |
| `409 SUMMARY_PARTIAL` | A source failed; cannot accept |
| `422 CONFLICTS_NOT_ACKNOWLEDGED` | Accepting with conflicts needs the acknowledgement |

Accepting **changes nothing in triage**: no `triage_runs` or `review_events` row is written, and urgency and sign-off stay on the triage run (docs/17).

Audit actions `encounter_summary_generated` and `encounter_summary_reviewed` record counts and enums only.

Migration step 13 adds `encounter_summaries` and `encounter_summary_review_events`. Both are append-only.

## 5. Reviewer screen (`frontend/src/components/review/EncounterSummaryPanel.tsx`)

- **Placement:** in the case workspace, between "Why the rules gave X" and the recorded input.
- **Page order:**
  1. "Needs attention before relying on this summary": unavailable sources, conflicts, missing danger signs, no triage run or triage older than the evidence (with a plain action), withheld sentences, and items waiting for a check.
  2. The **rules engine's urgency**, kept apart from the drafted text.
  3. The handover sentences.
  4. Each section as rows, with a trust pill and expandable sources.
  5. Missing information with "why it matters".
  6. The trust legend.
- **Conflicting rows** say "Sources disagree — no value chosen" and list every candidate.
- **Accept** is disabled while the summary is stale or partial. With conflicts, it needs a tick box. View logic is in `src/lib/encounterSummary.ts` and has tests.

## 6. Small changes beside it

- **Prescription corrections:** a reviewer can now correct `frequency_raw` and `duration_raw`, from closed values only: `OD BD TDS QID HS SOS STAT PRN` and "N days/weeks/months". A drug name still cannot be corrected; reject the row instead.
- **Voice:** `voice.service.case_resolutions` was factored out of `prefill`, whose output is unchanged.

## 7. Research basis (format guidance only, not an accuracy claim)

- **Microsoft Dragon Copilot** drafts sectioned notes from ambient speech and only fills sections that have content ([about](https://learn.microsoft.com/en-us/industry/healthcare/dragon-copilot/about/), [3.6 release notes](https://learn.microsoft.com/en-us/industry/healthcare/dragon-copilot/whats-new/3-6)). We borrowed the idea of sectioning and of skipping empty sections. Its accuracy is not ours.
- **"A framework to assess clinical safety and hallucination rates of LLMs for medical text summarisation", *npj Digital Medicine* 2025:** a framework for clinical-safety and hallucination rates in LLM note summarisation ([link](https://www.nature.com/articles/s41746-025-01670-7)). In their experiments, an LLM-driven fact "atomisation" step *increased* hallucinations and omissions. Our facts come from stored, human-reviewable records, not from an LLM, and the prose is a template, so that failure mode does not arise. It would return if an LLM writer were added.
- **I-PASS** structured handoff: illness severity, patient summary, action list, situation awareness, synthesis by receiver. There is moderate-certainty evidence that structured handoff improves safety outcomes (AHRQ Making Healthcare Safer IV, [NBK613742](https://www.ncbi.nlm.nih.gov/books/NBK613742/)). We map it as: severity = the rules urgency, shown first; summary = the sections; action list = "Needs attention" and missing information. Synthesis by the receiver is the human review.

## 8. Evaluation (synthetic; `scripts/eval_encounter_summary.py`)

**Setup:**
- 18 hand-written cases in `tests/ai/fixtures/encounter_cases.json`.
- Run through the real API, with the fake provider (a keyword extractor, **not an LLM**) and replayed OCR.
- Each metric shows passed / total.

**Results (2026-10-11):**

| Metric | Result |
|---|---|
| supported_sentences (cites facts; numbers in the cited facts; no unchecked document value) | 55 / 55 |
| traceable_facts | 112 / 112 |
| urgency_matches_rules | 18 / 18 |
| duration_exact | 5 / 5 |
| duration_symptom_link | 3 / 3 |
| symptom_precision | 10 / 10 |
| symptom_recall | 10 / 10 (8 / 10 before fix R11) |
| numeric_exact (vitals and labs) | 7 / 7 |
| medication_exact | 1 / 1 |
| handwriting_abstention (unchecked or uncertain lines shown, flagged, never in prose) | 4 / 4 |
| conflict_detection | 3 / 3 |
| conflicting_values_not_narrated | 2 / 2 |
| missing_info_listed | 3 / 3 |
| pending_surfaced | 2 / 2 |
| unattested_values_hidden | 1 / 1 |
| dedupe_correct | 1 / 1 |
| dedupe_keeps_all_sources | 1 / 1 |
| lab_date_kept | 2 / 2 |
| rejected_values_dropped | 1 / 1 |
| consent_respected | 1 / 1 |
| abstains_when_unit_or_value_unclear | 1 / 1 |
| unchecked_kept_out_of_prose | 1 / 1 |

**Limits:**
- The denominators are tiny and the cases were written by the developer.
- Extraction quality here is the fake provider's.
- Handwriting accuracy was **not** evaluated: the prescription is one synthetic fixture with replayed engine output.
- None of this is clinical validation. A real-document evaluation needs the governed plan in docs/15.

## 9. Limits and items needing human review

- **Privacy change R11 needs a privacy reviewer's sign-off.**
  - It narrows NER redaction for an exact list of clinical words.
  - The risk is a person or place whose whole name is exactly one of those words, for example someone called "Pulse".
  - Pattern identifiers, the name-context heuristics, and the residual sweep are unchanged.
- **Duration** is linked to a symptom only on a single-symptom sentence. Otherwise it stays case-level. No onset date is ever computed, because the reference time is unknown.
- **The gate checks consistency, not truth.** It proves that each sentence matches its cited facts. It cannot catch a wrong fact that a human confirmed, or a misread value that nobody checked (those are tagged).
- **Triage vitals:** values from earlier triage runs are shown as history. A change over time is not flagged as a conflict.
- **Reviewer role** on triage-form facts is not stored per field: the triage run records an actor id only.
- **Snapshots:** the summary is a snapshot. It is not regenerated automatically; the screen shows "Out of date" and offers Refresh.
- **Languages:** English only. Hindi and Odia transcripts reach it only through the existing translation path (docs/16).
