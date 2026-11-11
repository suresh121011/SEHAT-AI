# Remaining work: completion report

> 2026-11-11. Branch `feat/summary-note-all-sources`, worktree `.worktrees/summary-note`, base `origin/main` `0b5b23b`. **Nothing is committed and there is no pull request.**
> Research prototype, not a clinically validated device. Non-diagnostic. Not production-ready. Every run below used synthetic data on a scratch database.

Status words used here:

- **IMPLEMENTED AND VERIFIED**: built, and checked by running it.
- **IMPLEMENTED BUT NOT VERIFIED**: built; the stated check was not run.
- **VERIFIED WITH MOCK**: checked only against a fake or replayed engine.
- **BLOCKED BY CREDENTIALS**, **BLOCKED BY HARDWARE**: could not be run here.
- **DEFERRED BY DESIGN**: left out on the owner's decision.

## Summary

| # | Item from the request | Status |
|---|---|---|
| 1 | Print: dialog and printed output | IMPLEMENTED AND VERIFIED for the button, the print layout and the A4 output. The native print dialog itself is IMPLEMENTED BUT NOT VERIFIED (automation cannot drive it) |
| 2 | Image description from a real engine | IMPLEMENTED AND VERIFIED (runtime only, one synthetic picture) |
| 3 | Real OCR engine run | IMPLEMENTED AND VERIFIED (synthetic pages) |
| 4 | Real speech engine run | IMPLEMENTED AND VERIFIED: Hindi local; English and Hindi cloud (synthetic clips) |
| 5 | Flagged image in the browser | IMPLEMENTED AND VERIFIED. The flag comes from the fake backend's canned ECG text, so this part is VERIFIED WITH MOCK for the image text |
| 6 | Image and interview evaluation cases | IMPLEMENTED AND VERIFIED (23 new cases) |
| 7 | Evaluation rerun | IMPLEMENTED AND VERIFIED; VERIFIED WITH MOCK by nature (no real model runs in it) |
| 8 | CI skip-gate minimum | IMPLEMENTED AND VERIFIED locally. GitHub Actions: IMPLEMENTED BUT NOT VERIFIED (nothing was pushed) |
| 9 | `docs/18` §8 | IMPLEMENTED AND VERIFIED (read against the code) |
| 10 | Handwritten free-text notes | DEFERRED BY DESIGN |
| 11 | Interview screen | IMPLEMENTED AND VERIFIED |
| 12 | End-to-end browser verification | IMPLEMENTED AND VERIFIED: 30 / 30 in fake mode, 29 / 29 in real mode |

## A. Completed features

- **Interview screen** on the Questions step, connected to the existing interview API (§G).
- **Print** of the summary note as its own A4 page set (§F).
- **Out-of-date signal:** when an image is marked as reviewed, the summary on the same screen re-reads its state and shows "Out of date".
- **Source of image text:** each image row and the note say which backend and model wrote the description. Text from the fake backend is labelled "DEMO TEXT … no model looked at it".
- **New conflict rule:** a confirmed interview duration answer that falls outside a recorded, exactly parsed duration is a conflict. No value is chosen and neither is written in a sentence.
- **Evaluation corpus and runner** with image and interview cases (§H, §I).
- **CI skip gate:** higher minimum, three more required modules, counts in the log, five more gate tests (§J).
- **Browser check script** `backend/scripts/e2e_summary_note_browser_check.py` with a fake mode and a real mode.

No engine integration had to be written: the real OCR, image and speech backends already existed behind settings. What was missing was running them and checking the result, which is recorded in §D to §F.

## B. Files changed

Modified:

- `backend/app/ai/encounter_summary.py`
- `backend/scripts/ci_skip_gate.py`
- `backend/tests/ai/fixtures/encounter_cases.json`
- `backend/tests/ai/test_encounter_eval.py`
- `backend/tests/ai/test_encounter_summary_transparency.py`
- `backend/tests/ci/test_skip_gate.py`
- `docs/06_API_Data_Contracts.md`, `docs/18_Medical_Image_Pipeline.md`, `docs/19_Encounter_Summary.md`
- `frontend/src/app/globals.css`
- `frontend/src/app/intake/follow-up/page.tsx`
- `frontend/src/components/review/EncounterSummaryPanel.tsx`
- `frontend/src/components/review/EvidencePanel.tsx`
- `frontend/src/lib/encounterSummary.ts`, `frontend/src/lib/encounterSummary.test.ts`

New:

- `backend/scripts/e2e_summary_note_browser_check.py`
- `backend/tests/ai/test_encounter_summary_sources.py`
- `frontend/src/components/InterviewPanel.tsx`
- `frontend/src/lib/interview.ts`, `frontend/src/lib/interview.test.ts`
- `docs/plans/summary-note-all-sources.md`
- `docs/implementation/remaining-work-completion-report.md` and `docs/implementation/evidence/` (two PDFs, two screenshots; synthetic data only)

Not touched: `frontend/package.json`, the lock file, the unregistered WIP routers, the urgency rules, consent and audit rules, and `sehat-ai/sehat.db`.

## C. New dependencies

None in the project. For the browser check, Playwright, pypdf and Pillow were installed in a throwaway environment in the scratch directory, driving the installed Google Chrome.

## D. Real OCR execution results

All inputs are the repository's synthetic fixtures.

| Engine | Run | Result |
|---|---|---|
| PaddleOCR (PP-OCRv6, ONNX, CPU) | `SEHAT_LIVE_OCR=1` live test | Values exact 26 / 26; value region correct 26 / 26; median 1.05 s per page |
| Surya OCR 2 | same | 6 rows in 15.2 s including load |
| Chandra OCR 2 (MLX 8-bit) | same | 4 medicine lines in 31.5 s including load |
| PaddleOCR through the running app | browser check, real mode | Synthetic lab page read into 6 rows: haemoglobin 11.2, WBC 3400, platelets 85000, RBC 4.10, haematocrit 34.5, MCV 84.0. Every row stayed "machine-read, not yet checked" and appeared in the note with that label |

Limits: these are clean synthetic pages. Rotated, low-quality and real scanned pages were not run. This is not real-report accuracy; `docs/15` holds the governed plan for that.

## E. Real speech execution results

| Engine | Language | Clip | Result |
|---|---|---|---|
| Local IndicConformer 600M | Hindi | `hi_fever_102.wav` | Transcribed in 7.0 s (first call). Character error rate 0.21 against the script. Both numbers found (3 days, 102) |
| Local IndicConformer 600M | Hindi | `hi_fever_saadhe.wav` | 0.3 s. Character error rate 0.07. Pulse 120 found; the "half" modifier on the temperature was flagged as unparsed |
| Local, through the running app | Hindi | `hi_fever_102.wav` | `completed`; two candidate values offered for read-back, none confirmed automatically |
| Local | English | `en_fever_102.wav` | Refused with `LANGUAGE_UNSUPPORTED` (422). The local model has no English |
| Local, through the app | none | 2 s of silence | `no_speech`, no transcript |
| Local, through the app | none | bytes that are not audio | Refused (400) |
| Sarvam (cloud) | English | `en_fever_102.wav`, `en_pulse_oxygen.wav` | Transcribed in 6.1 s and 4.0 s. Duration 3, temperature 102, pulse 120, SpO2 92 found |
| Sarvam (cloud) | Hindi | both Hindi clips | Character error rate 0.00 and 0.33; numbers found |

Notes:

- The cloud run used the project's own opt-in test (`SEHAT_LIVE_SARVAM=1`) with the key already configured for this workspace. It sends synthetic audio to Sarvam and costs a small amount. The key was never printed.
- English speech therefore needs the cloud engine and `voice_cloud` consent. There is no local English path.
- Odia was not run: the local test has no Odia clip in this checkout's fixture set that it selects.
- Long audio and a provider timeout were not run against a real engine; they are covered by the existing unit tests with stubbed engines.
- Two clips per language are a smoke test, not a measure of accuracy.

## F. Browser verification results

Script: `backend/scripts/e2e_summary_note_browser_check.py`, headless Google Chrome, scratch database.

**Fake mode (`MEDGEMMA_BACKEND=fake`): 30 / 30 passed.**

- Interview: status and progress; no save without the confirmation tick; preview; review list; a skipped question stays missing; answers survive a reload; an answer containing a name and phone number is refused and nothing is stored; pause and resume; a correction replaces the earlier answer.
- Flagged image: the canned ECG text raises a flag; sign-off is refused with `409 IMAGE_FINDINGS_NOT_REVIEWED`; the summary lists it under "Needs attention"; the described text, the flag reason and a link to the stored image are visible; the image opens; ticking "Findings reviewed" marks the summary out of date; the refreshed summary shows the reviewed state; the review persists; sign-off is no longer blocked by the image.
- Print: see below.
- Audit: `interview_answer_recorded`, `encounter_summary_generated` and `medgemma_findings_acknowledged` are present; the log holds no answer text and no finding text.
- No unexpected console errors.

**Real mode (local image model, real OCR, local speech): 29 / 29 passed.** The interview and print checks repeat; the engine checks are in §D and §E. The local MedGemma 1.5 model (MLX 8-bit) described a procedurally drawn chest-like picture; its text arrived labelled with the model name, "not yet reviewed" and "not a validated medical device".

**Print.**

- Clicking Print calls `window.print()` exactly once.
- The printed copy holds this visit's note, its review state, the visit id, the print time and the disclaimer. A second visit's id is not in it.
- Under print media only the note is laid out: no navigation, no buttons, no other panel.
- Chrome writes it as an A4 PDF (595 × 842 pt), 2 pages, with the section headings and no screen controls. See `evidence/summary_note_real.pdf` and `evidence/summary_note_fake.pdf`.
- **Not verified:** the operating-system print dialog and paper output. Automation cannot drive that dialog; the checks above stand in for it. This needs one manual look (§P).

**What the image review supports.** An image description can be marked as reviewed or deleted. It cannot be corrected field by field, and there is no "reject" or "uncertain" for it. Field-level confirm, correct, reject and unsure exist for document rows (lab values and prescription lines), which the earlier `e2e_real_app_browser_check.py` covers. That script was not rerun in this work.

**Patient identity mismatch** was checked in the evaluation corpus (`lab_identity_mismatch`), not in the browser.

## G. Interview screen implementation

- **Where:** the Questions step (`/intake/follow-up`), above the AI section. It needs triage consent only, so it is also shown when AI assistance was declined.
- **What it shows:** status, language, a progress bar, counts of answered, "not sure" and not-yet-asked questions; the health worker's own tasks (measurements, danger-sign screens) as a list that is never answered here.
- **Answering:** one fixed question at a time; listed choices or free text up to 500 characters; who answered; a preview of what will be recorded; a read-back tick that is required before saving.
- **Voice:** a text answer can be taken from a completed voice recording of the same visit. The text is copied in for checking and the answer cites that recording. Recording inside the interview itself is not built.
- **Controls:** start, previous, confirm and next, skip, pause, resume, review, change.
- **API:** `GET /cases/{id}/interview`, `POST /cases/{id}/interview/answers`, unchanged. No new endpoint.
- **Differences from the request, and why:**
  - There is no "complete interview" call. The API has none, and completion is derived: every patient question has a confirmed answer. "Save progress" is automatic, since each confirmed answer is stored at once.
  - English only. Hindi and Odia wording is still awaiting native-speaker review (owner decision I1).
  - Text answers are stored as said. They are not turned into structured symptoms, and they never enter the summary's prose (owner decision I4).
  - Questions are optional; there is no required/optional flag in the script, so every question can be skipped and stays listed as missing.
- **Known limit found by the evaluation:** a romanised Hindi answer such as "teen din se bukhar hai" is refused by the identifier check, which reads the words as possible names. Nothing is stored. Changing that check is a privacy decision and was left alone.

## H. Evaluation corpus additions

23 cases added to `backend/tests/ai/fixtures/encounter_cases.json` (18 → 41).

- **Image and document (11):** identity mismatch, a value marked unsure, a described X-ray, a flagged X-ray, a flagged ECG marked as reviewed, a wound with a yellow flag, diagnostic text withheld, a negated finding that needs no review, description excluded without AI consent, a deleted image, two images listed separately.
- **Interview (12):** fever with duration, a duration that agrees with typed text, a duration that contradicts it, a symptom denial kept as said, a historical symptom kept as said, "not sure", a missing duration, a patient correction, completion, a caregiver as speaker, the romanised Hindi refusal, an interview without a triage run.

Not covered, and why:

- **Rotated, low-quality and unit-conflict images:** these test the OCR engine, and the corpus replays stored engine output. They need the real-document evaluation of `docs/15`.
- **Spoken Hindi or mixed-language interview answers:** the script is English only.
- **Normalised concepts for interview text:** the product does not normalise interview answers, so there is nothing to score.

Expected values were written from the intended behaviour before the cases were run. Three labels were then changed, and each change is recorded here:

- Two image cases expected "no flag" for the fake X-ray text. That text contains a negated mention, which is a review note, not a flag needing review. The label was redefined as "needs review" (`requires_acknowledgement`), which is the rule in `docs/18` §8.
- The Hindi-words case expected the text to be stored. It is refused, so the case now pins the refusal and is marked as a known limit, not a success.

## I. Evaluation results

Run on 2026-11-11 with `scripts/eval_encounter_summary.py`. **Mode: mock and offline.** Text extraction is the fake keyword extractor, not a language model. Document values are replayed OCR output. Image text is replayed model output or the fake backend's text. No real model runs. Provider errors: 0.

All 40 metrics passed on 41 cases:

| Metric | Passed / total |
|:---|---:|
| supported_sentences | 106 / 106 |
| traceable_facts | 255 / 255 |
| urgency_matches_rules | 41 / 41 |
| note_covers_every_fact | 41 / 41 |
| note_states_each_fact_once | 41 / 41 |
| symptom_precision, symptom_recall | 11 / 11 each |
| duration_exact | 6 / 6 |
| numeric_exact | 7 / 7 |
| conflict_detection | 5 / 5 |
| conflicting_values_not_narrated | 4 / 4 |
| missing_info_listed | 10 / 10 |
| pending_surfaced | 5 / 5 |
| image_listed_with_review_state, image_source_linked | 9 / 9 each |
| image_flag_surfaced | 5 / 5 |
| image_findings_kept_out_of_prose | 4 / 4 |
| image_flag_never_changes_urgency | 1 / 1 |
| diagnostic_image_text_withheld | 1 / 1 |
| identity_mismatch_values_hidden | 1 / 1 |
| unsupported_statement_absent | 3 / 3 |
| interview_answer_exact | 12 / 12 |
| interview_never_in_prose | 17 / 17 |
| interview_source_linked, interview_verification_state | 14 / 14 each |
| answered_question_not_missing | 6 / 6 |
| not_sure_kept_as_unknown, interview_latest_answer_wins, interview_speaker_kept | 1 / 1 each |
| the other 11 earlier metrics | all passed |

Mean time per case, in process: image 24 ms, interview 19 ms, earlier summary cases 180 ms.

Against the earlier baseline (18 cases, 22 metrics, all passed on 2026-10-11): no regression. The older metrics kept their pass rate; some totals rose because more cases feed them.

Reading these numbers: the denominators are small and the cases are hand-written. They are a regression and safety check on the summary stage. They say nothing about how well any model reads an image, a page or a voice. There is no real-engine evaluation with gold labels; §D and §E are smoke runs.

## J. CI skip-gate changes

- **Minimum raised from 1520 to 1555.** The last CI run on `main` (`0b5b23b`) executed 1541 tests. This work adds 20 that run in CI, so 1561 are expected; the margin of 6 matches the earlier one. The history is written beside the number.
- **Three more required modules:** `tests.ai.test_encounter_summary_sources`, `tests.ai.test_encounter_summary_transparency`, `tests.ai.test_encounter_eval`. A skip in any of them fails the build.
- **Log line added:** total, executed, skipped, allowlisted skips, unexpected skips, minimum, and the count of required safety tests executed and skipped.
- **Five more gate tests:** the counts; one below the minimum fails and exactly the minimum passes; skipped tests never count towards the minimum; the minimum and the required list cannot drop unnoticed; every required module exists on disk.
- **Local result:** `total=1623 executed=1604 skipped=19 allowlisted_skips=19 unexpected_skips=0 minimum_required=1555 | safety: modules=24 tests_executed=352 tests_skipped=0`, then `skip gate: ok`.
- **Not verified:** GitHub Actions. Nothing was pushed. CI installs fewer optional models than this machine, so its executed count will be lower than 1604; the estimate is 1561.

## K. `docs/18` §8 changes

- **§8.1** added: where the reviewer sees an image, what happens after "Findings reviewed", what "reviewed" means, and that the source of the text is always shown.
- **§8.2** added: a table of each item's state and how it was checked, what is needed to repeat the real-engine runs, and the known limits.
- `docs/19` §8 and §10 and `docs/06` were updated to match.

## L. Security review

- **No control was weakened.** Authentication, facility scope, consent checks, rate limits, the identifier check on answers and the audit rules are unchanged.
- **Interview screen:** uses the same-origin proxy and the session cookie. Only the visit's health worker can record. The server re-validates every answer. Answer text never reaches the audit log (checked in the browser run).
- **Summary:** still writes no triage, review, interview or image rows (tested). It carries no name, phone number or patient code.
- **Image text:** stays out of written sentences; a flag never changes urgency (tested in the corpus).
- **Print:** prints a copy of the current note only; another visit's id was checked to be absent.
- **Cross-screen event:** the "evidence changed" event carries only the visit id and triggers a read.
- **Secrets:** no key was printed or copied. For the real runs the worktree was linked to the main checkout's settings file and models; the links were removed afterwards.
- **Open point for a reviewer:** the browser check signs in with the shared demo accounts, as the existing scripts do. It must only ever be pointed at a scratch database.

## M. Test results

| Check | Result |
|---|---|
| Backend suite | 1604 passed, 14 skipped (all allowlisted opt-in live tests), 5 expected failures |
| Skip gate | ok |
| Frontend unit tests | 140 passed |
| TypeScript | clean |
| ESLint | 0 errors, 3 warnings that were there before |
| Evaluation | 41 cases, all metrics passed (mock) |
| Live OCR tests | 3 passed |
| Live local speech tests | 2 passed, 1 skipped (no Odia clip selected) |
| Live cloud speech tests | 2 passed, 1 skipped |
| Live local image-model test | 1 passed |
| Browser check, fake mode | 30 / 30 |
| Browser check, real mode | 29 / 29 |

## N. Git branch and commit status

- Branch `feat/summary-note-all-sources`, tracking `origin/main`, in the worktree `/Applications/Developer/Hackathon/.worktrees/summary-note`.
- **Uncommitted:** 15 modified files and the new files listed in §B. No commit, no push, no pull request, no merge.
- The main checkout (`sehat-ai/`, branch `feat/voice-agent-multimodal`) and its uncommitted work were not changed.
- No reset, clean, stash or bulk add was used.

## O. Remaining blockers

- **Native print dialog and paper:** not automatable here.
- **GitHub Actions for the new gate:** needs a push, which needs the owner's word.
- **Flag from a real image model:** a real model's flag depends on what it writes. No synthetic picture was found that makes it raise one, so the flagged workflow was verified with the fake backend's text only.
- **English speech without the cloud:** not possible with the installed local model.
- **Handwritten free-text notes:** DEFERRED BY DESIGN.
- **Romanised Hindi in typed interview answers:** refused by the identifier check; needs a privacy decision.

Nothing was BLOCKED BY CREDENTIALS or BLOCKED BY HARDWARE.

## P. Exact manual steps still required

1. **Review the diff** in the worktree, then say "commit this" if it should be committed.
2. **Print once by hand:** open a case as the medical officer, press Print on the summary note, and confirm that the system dialog opens and the paper shows only the note.
3. **Push and open a pull request** when ready, and confirm that the "backend tests" job prints the gate counts and passes with the minimum of 1555. If CI executes fewer than 1555, compare its count with the estimate of 1561 before changing any number.
4. **Name a reviewer** for the gate change and the new conflict rule, as for any safety-relevant change.
5. **To repeat the browser check:** follow the header of `backend/scripts/e2e_summary_note_browser_check.py`. Open the app on `127.0.0.1`, not `localhost`, so its cookies stay apart from another instance on the same machine.
6. **Decide** whether typed Hindi words should be accepted in interview answers, and who reviews the Hindi and Odia question wording.
