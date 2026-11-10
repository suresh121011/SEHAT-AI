# Encounter summary: clinical review checklist

> **For a qualified clinician to complete. Nothing in this file is clinical approval.** SEHAT AI is a research prototype and a non-diagnostic triage aid; urgency comes only from the deterministic rules engine. The engineering team has **not** validated the summary wording clinically, and claims no clinical validation, efficacy or diagnostic accuracy.
> Code: `backend/app/ai/encounter_summary.py`, schema `encounter-summary-2026-11-10.3`.
> Engineering tests: `backend/tests/ai/test_encounter_summary_semantics.py`. These test behaviour, not clinical adequacy.

## Reviewer

| Field | Entry |
|---|---|
| Reviewer role (for example MBBS medical officer or nurse practitioner) | |
| Review date | |
| Material reviewed (screenshots or synthetic cases) | |
| Language(s) reviewed (en / hi / or) | |

## Items to review

For each item, record **Acceptable / Needs change / Unsafe**, your feedback and any requested revision.

| # | Item | What the summary does today | Assessment | Feedback | Requested revision |
|---|---|---|---|---|---|
| 1 | "Reported at intake:" wording | Lists symptoms recorded as present. It makes no claim about who spoke (patient, caregiver or ANM). | | | |
| 2 | "Denied at intake:" wording | Lists symptoms recorded as explicitly denied. A denied symptom never appears as present. | | | |
| 3 | Withheld uncertain symptoms | If the model and the text disagree about negation, or a past-time cue is present, the symptom is **left out of the prose** and listed under "withheld" ("not established whether this was present, denied or in the past") until a person decides it. | | | |
| 4 | Historical versus current | Temporal attributes are **not modelled**. Only flagged past-time mentions are withheld. A patient's past visits are listed by date and labelled as past records. | | | |
| 5 | Source attribution | Each value carries its source and trust level ("not yet checked", "keyword match", "machine-read"). Unchecked document values stay out of the prose. | | | |
| 6 | Missing or conflicting information | Conflicting values are shown with no value chosen ("Sources disagree… see conflicts"). Missing required fields come from the rules engine. | | | |
| 7 | Readability and clinical usefulness | Template sentences, one per section. | | | |
| 8 | Anything that reads as a diagnosis, prescription or advice | The gate blocks diagnostic or prescriptive language. Please report any that gets through. | | | |

## Resolution log (engineering)

| Date | Item # | Change made | Who confirmed with the reviewer | Status (open / resolved / declined, with reason) |
|---|---|---|---|---|
| | | | | |

**Rules for the engineering team:**
- Do not change clinical rules or urgency logic in response to this review without explicit owner approval.
- Record every wording change here, with a test.
