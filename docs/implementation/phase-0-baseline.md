# Phase 0 baseline and P0-1 scope (lab-table parser)

> Recorded 2026-11-04 before any production change on branch `fix/p0-1-lab-parser`. Source plan:
> `/Applications/Developer/Hackathon/SEHAT_AI_Architecture_Audit_and_Plan.pdf` (rev. 2, accessible; task H0-2 there).

## 1. Instructions read

- `AGENTS.md`, in both the workspace and `sehat-ai/`.
- `docs/14_OCR_Pipeline.md` §5 (the lab grammar).
- `docs/19_Encounter_Summary.md`.
- The `fullstack-ai-software-engineer` skill.

Not available:
- There is no `CLAUDE.md` in the repository or the workspace.
- There is no `software-engineering` skill: `.agents/plugins/local-skills/skills/` holds only `fullstack-ai-software-engineer`, `llm-council` and `using-clavix`.
- `llm-council` was not needed, because there was no architectural disagreement in this task.

## 2. Git state

**Branch.** `fix/p0-1-lab-parser` was created with `git switch -c` from `feat/clinical-encounter-summary` at `43cbfa8`. Nothing was stashed, reset or committed.

**Uncommitted work carried over unchanged.** None of it belongs to this task, and none of it is touched:
- Modified: `backend/app/main.py`, which registers the 4 work-in-progress routers.
- Modified: `frontend/package.json`, where turbopack was removed from the scripts.
- Untracked: `backend/app/{cds,patient,surveillance}/`, `backend/app/routes/{cds,patient,referrals,surveillance}.py`, `backend/tests/{cds,patient,surveillance}/` and `public/`.

**Recent commits.** `43cbfa8`, `6a6f528`, `f95b603`, `9ca5dd5`, `bb5651a`.

## 3. Test baseline (before the change)

| Suite | Result |
|---|---|
| Backend, full suite (`../.venv/bin/python -m pytest -q`) | **1433 passed, 2 failed**, 14 skipped, 5 xfailed |
| OCR and related tests (`tests/ocr`, `tests/ai/test_ocr_merge.py`, `tests/ai/test_encounter_summary.py`) | 244 passed, 1 skipped |
| Frontend (`npm test`) | 100 passed, 0 failed |
| Lint and type checks | None configured for the backend (`requirements-dev.txt` lists only pytest, pytest-asyncio and httpx), so none were run |

The 2 backend failures existed **before** this task. Both come from the uncommitted work-in-progress routers registered in `main.py` (`/cds/*`, `/referrals/*`, `/patient/*`, `/surveillance/*`), which accept unconstrained free-text fields:

- `tests/ai/test_api.py::test_only_two_request_fields_carry_free_text` fails with `'/api/v1/cds/scores.score_name'`.
- `tests/privacy/test_boundary.py::test_no_route_accepts_free_text_for_ai` fails with 35 unconstrained string fields, all in the work-in-progress routers.

They are out of scope here (plan task H0-1) and are not fixed by this task.

## 4. Parser locations and data path

| Stage | Location |
|---|---|
| OCR words → rows → columns → `LabCandidate` | `backend/app/ocr/extract.py`: `_rows`, `_header_anchors`, `_column`, `_split_value_flag`, `_candidate`, `extract_lab` |
| Value, unit, range and flag grammar | `backend/app/ocr/parse.py`: `parse_value`, `parse_unit`, `parse_range`, `parse_flag` (uses `FLAG_TOKENS`) |
| Analyte names and header words | `backend/app/ocr/lexicon.py`: `ANALYTE_ALIASES`, `analyte_key`, `HEADER_WORDS`, `FLAG_TOKENS` |
| Second-engine rows (Surya) and their analyte key | `backend/app/ocr/surya_parse.py`: `TableRow.analyte_key`, `table_rows` |
| Cross-engine checks, bands and flag-vs-range consistency | `backend/app/ocr/verify.py`: `verify_lab` |
| Persisted payload | `backend/app/ocr/service.py` `_lab_payload` → `ocr_fields.payload_json`, with `name_raw`, `analyte_key`, `value`, `unit`, `range`, `flag_raw`, `printed_flag`, `flags` and the regions in `regions_json` |
| Summary evidence source | `backend/app/ai/encounter_summary.py` `_ocr_source`, which keys lab facts on `analyte_key or name_raw` and shows `printed_flag` |
| Existing tests | `tests/ocr/test_extract.py` (layout replay from `tests/fixtures/ocr/*.truth.json`), `test_parse.py`, `test_verify.py`, `test_prose.py`, `test_api.py` |

## 5. Root causes (confirmed by new failing tests)

1. **The flag lands in the name column.** On a report with no FLAG header (TEST | VALUE | UNIT | REFERENCE), the printed `L` or `H` is its own OCR word, just left of the value anchor.
   - `_column` assigns it to `name`, giving `name_raw = "SERUM UREA L"`, so `analyte_key` is `None` and `printed_flag` is lost.
   - `_split_value_flag` only handles a flag glued to the *value* column.
   - Local evidence: the synthetic `sehat.db` has `SERUM UREA L` and `SERUM CREATININE L` rows, with the `L` as a separate word region at x≈200.
2. **Explanation paragraphs become rows.** After a header, `extract_lab` keeps any row with a non-empty value cell. A wrapped paragraph puts its second half in the value column ("in muscle from creatine phosphate."), so prose, list items and footers become `unrecognized_analyte` rows. Many of them land in the `human_entry` band. Local evidence: 180 of 216 lab rows have `analyte_key = NULL`.

## 6. P0-1 scope (exactly)

**In scope:**
- **Split a trailing flag out of the name**, only when all of these hold:
  - the last token parses as a flag;
  - no flag is already present;
  - the full name is *not* a known analyte;
  - the name without the flag *is* a known analyte.
  - The original word and its region move to the `flag` role, and the row is tagged `flag_in_name_column`.
  - Nothing is stripped when the result would still be unrecognised, so no flag is invented.
- **Skip prose rows**, only when all of these hold:
  - the test name is unknown;
  - the value cell is not a numeric or qualitative result;
  - the value cell reads as words: at least two tokens, one of them a word of 3 or more letters.
  - Rows after a paragraph are still parsed.
  - Unknown tests with a number or a short code (`EGFR 134.19`, `EGFR CATEGORY G1`) are kept for review.
- **Apply the same name rule to second-engine rows**, so cross-engine matching by analyte key still works.

**Out of scope:**
- Lexicon additions such as eGFR, BUN and ratios: they need sourced reference ranges and a clinician check.
- Summary logic.
- Urgency rules.
- Schema, API or database changes.
- Everything in P0-2 and later.

## 7. Risks

- **A legitimate test whose name ends in a flag-like word.** This is mitigated by the "full name unknown, stripped name known" rule. The existing `printed_flag_consistency` check in `verify.py` also fails a split flag that disagrees with the printed range.
- **An unknown test whose printed value is several words** (for example "see note") would now be skipped. Such a row carries no usable result and the page image is kept, but it no longer appears as a reviewable row. This is listed as a limitation.
- **The fix is tuned on one report layout.** A frozen set of varied printouts is a follow-up (plan H0-2).

## 8. P0-1 outcome (2026-11-04, uncommitted on `fix/p0-1-lab-parser`)

**Tests were written first.** 6 of the 10 new parser tests failed on the original parser, for the intended reasons:

- `SERUM UREA L` was not recognised and had no flag region.
- Paragraph and footer rows were extracted.
- The Surya row name did not match.

The 4 guard tests passed both before and after the change.

The end-to-end test `test_lab_flag_end_to_end.py` also failed on the original parser: only calcium was recognised.

**Production changes (3 files, 45 lines added and 5 removed):**

- `lexicon.py`: `trailing_flag_split`.
- `extract.py`: `_split_name_flag` and `_prose_row`, applied in `_candidate`.
- `surya_parse.py`: `TableRow.analyte_key` uses the same split.
- Schema, API, persistence, summary and urgency code are unchanged.
- One new value appears in the candidate `flags` list: `flag_in_name_column`.

**End-to-end data path (with the test engines):**

1. Upload.
2. Parser: urea, creatinine and calcium are recognised, and both paragraphs are dropped.
3. Verification: `second_engine_agreement` passes and `printed_flag_consistency` passes (L agrees with 17 < 19).
4. Storage in `ocr_fields`: `name_raw "SERUM UREA"`, `printed_flag "L"`.
5. Attestation and confirmation.
6. Encounter summary: `lab:urea` is a `document_confirmed` fact with flag L. Unconfirmed creatinine stays `document_unchecked`, in the tables only.

**Follow-ups found (not done):**

- Lexicon entries for eGFR, BUN and ratios need sourced ranges.
- Rows already stored in `sehat.db` are not re-parsed. Only new uploads use the fix.
- A name delivered as one OCR line rather than separate words ("SERUM UREA L" as a single token) is not split by the table parser and stays unrecognised. The Surya side handles it as text.
- The fix needs checking on more report layouts with a frozen set of varied printouts.
- The summary still needs a person to attest the document and confirm each row (P0-2).
