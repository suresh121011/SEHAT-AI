# 22. Lab timeline across reports and visits

> **Status:** built in Phase 3 (brief item 2: "read lab reports, pull key values, timeline with flagged gaps").
> Synthetic data only. View only: the timeline never produces or changes triage or urgency.

## 1. What it does

`GET /cases/{case_id}/lab-timeline` (the visit's ANM or a medical officer) returns one row per lab test with its
results in date order, from this visit and the patient's linked visits, and a list of gaps. The medical officer sees it
as the **Lab timeline** panel in the case workspace.

| Part | Rule |
|:---|:---|
| Which values | Only values a person confirmed or corrected, on documents a person confirmed belong to this patient (identity check "matches"). The same rule as `GET /cases/{id}/documents/reviewed`: both call `app.ocr.service.reviewed_items` |
| Which visits | This visit, plus the patient's other linked visits when this visit's `link_visits` consent is in effect, the viewer may open each visit, and each visit's own `link_visits` consent is in effect (`app.patient_registry`) |
| Date of a result | The report date printed on the report, read day-first as Indian reports print it (`12/10/2026` is 12 October; a two-digit year is 20xx); else the sample collection date; else the visit date. `date_source` says which. **Printed dates are read by the machine and nobody checks them:** each result says so (`date_machine_read`) with the date's page regions, and the screen says "read by the machine, not checked". A date after the visit is not used |
| Comparison | With the previous result of the same test only when both are plain numbers in the same unit **and** the date order agrees with the order the reports were added (so a misread date can never reverse a direction): **higher**, **lower** or **same**, with the previous value. Otherwise "not compared" and why |
| Range flags | As the OCR checks recorded them: below or above the range printed on that report, and below or above the cited reference interval (`app/rules/reference_ranges.py`). A value with no comparable range says "Not compared with a range", so no flag never reads as "within range" |
| Source | Every result carries its document, field, page and highlighted regions, so it can be traced to the page |

## 2. Gaps it names

| Gap | Meaning |
|:---|:---|
| `awaiting_check` | Values read by the machine and not yet checked by a person: counted, not shown |
| `document_not_checked` | Documents not yet confirmed as this patient's: their values are not shown |
| `date_from_visit` | Results placed by the visit date because no report or collection date could be read |
| `date_unreadable` | Documents whose printed date could not be read (never guessed) |
| `date_after_visit` | Documents showing a date after the visit: the visit date is used instead |
| `dates_machine_read` | Report dates that were read by the machine and not checked by a person, although they set the order |
| `not_in_latest_report` (per test) | The test's last result is from a report dated earlier than the latest report (same-day reports are not flagged) |
| Other visits | "Not linked to a patient record" or "consent to link visits is not in effect" |

## 3. What it does not do

- No "improving", "worsening", "critical" or trend words: those are clinical judgements. The owner's unregistered
  work-in-progress module `app/cds/lab_timeline.py` uses such labels; it is not used or changed.
- No "test overdue" or "repeat due" flag: there is no sourced re-test interval in the project, so any number would be
  invented. It can be added only with a cited source and the owner's approval (Agent Rule 8).
- No urgency, no change to triage, no model call.

## 4. Limits

- Dates are numeric day-month-year only (the shapes the OCR reads). A month-first date would be misread; Indian
  reports print day first. Report dates are not checked by a person in this prototype; a date check would need a
  review step of its own.
- Values are compared only within the same unit key; no unit conversion is attempted.
- The timeline is as good as the checks behind it: synthetic reports only, and no real-document OCR evaluation yet
  (`docs/15`).

## 5. Verification

- `backend/tests/lab_timeline/test_timeline_pure.py` (22 tests): date reading, grouping and order across visits,
  comparisons, a misread date that must not reverse a direction, dates after the visit, same-day reports, unchecked
  dates marked, no-range flag, gaps, no urgency field, plain labels.
- `backend/tests/lab_timeline/test_timeline_api.py` (4 tests, real API with OCR engines replayed): checked values only
  with their source, an unconfirmed document shows nothing, linked visits join one timeline and leave it when
  `link_visits` is withdrawn, roles, facility scope and consent (also re-checked inside the read snapshot).
- `tests/review/test_facility.py` probes the route for facility isolation. Both modules are required in the CI skip gate.
- `frontend/src/lib/labTimeline.test.ts` (4 tests) for the wording.
- An independent review-only safety review found two blocking issues (no consent re-check inside the read snapshot;
  unchecked machine-read dates deciding order and direction without saying so); both are fixed, with the warnings.
