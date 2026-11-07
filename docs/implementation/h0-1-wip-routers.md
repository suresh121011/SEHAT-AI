# H0-1: Work-in-progress routers unregistered until secured

> Decided 2026-11-04 by the project owner, from these options: unregister all 4, secure some, or secure all.
> The branch is `fix/p0-1-lab-parser`, after H1-3 (`2c42205`).

## 1. What was done

The 4 uncommitted router registrations were removed from `backend/app/main.py`, so it is identical to the committed version again. **Nothing else was touched.** These work-in-progress files stay exactly as they were: untracked, uncommitted, and still importable by their own unit tests.

- `backend/app/routes/cds.py`
- `backend/app/routes/referrals.py`
- `backend/app/routes/patient.py`
- `backend/app/routes/surveillance.py`
- `backend/app/cds/`, `backend/app/patient/`, `backend/app/surveillance/`
- their tests

None of their endpoints are reachable while unregistered. Both pre-existing failing tests now pass:

- `test_only_two_request_fields_carry_free_text`
- `test_no_route_accepts_free_text_for_ai`

To re-enable a router, add its registration back to `main.py`:

```python
from app.routes import ai, audit, auth, cases, cds, health, ocr, patient, referrals, review, surveillance, triage, voice
...
    app.include_router(cds.router, prefix=API_PREFIX)
    app.include_router(referrals.router, prefix=API_PREFIX)
    app.include_router(patient.router, prefix=API_PREFIX)
    app.include_router(surveillance.router, prefix=API_PREFIX)
```

Re-enable a router only after its items below are fixed. The privacy-boundary tests will fail again otherwise.

## 2. What each router needs before it is re-registered (found 2026-11-04)

| Router | Problem | Needed |
|---|---|---|
| `/referrals` | `GET /referrals`, `/stats` and `/overdue` are open to **any logged-in role, including patient**, across **all facilities**. Create and status-change have no `load_case`, facility scope, consent check or audit. Errors come back as HTTP 200 `{"error": …}`. `reason` and `notes` are accepted but dropped. Free-text fields are unconstrained. The status change is an in-place `UPDATE`. | Case access and facility scope (as in `consent.load_case`), consent, audit events, constrained fields, proper 4xx errors, and append-only status events. Add the routes to `tests/review/test_facility.py`. |
| `/cds/scores` | Returns `suggested_urgency`, a second urgency source beside the rules engine (AGENTS.md rule 5). `parameters` is an open dict. Exception text is echoed to the client. | Typed inputs per score; no urgency output, or a clearly separate "score band" that never feeds urgency; safe errors; sourced thresholds checked by a clinician. |
| `/cds/drug-interactions` | Open free-text medication and condition lists; not tied to a case. | Constrained inputs (for example RxNorm ids or a closed list), binding to a case with access and consent, and a licensed, curated source. |
| `/cds/possibilities` | Returns condition names, **ICD-10 codes and differential considerations**. This conflicts with AGENTS.md rule 2 (non-diagnostic); the plan placed "clinical possibilities" out of scope. | A product decision first. It cannot be re-enabled in its current form. |
| `/cds/fhir-export` | Builds a bundle from arbitrary caller-supplied clinical content, including a free-text `encounter_summary`, from any clinical role; not tied to a case. | Build only from a case's own stored, reviewed records, behind case access and consent; validate against a FHIR sandbox. |
| `/patient/summary` | Any logged-in role, including patient, can post arbitrary symptoms, medications and instructions. Not tied to a case. The Hindi and Odia wording is unreviewed. | Bind to a case and its signed-off record; restrict roles; native-speaker and clinical review (docs/13). |
| `/surveillance` | In-memory state, lost on restart. Free-text condition and district. The dashboard is readable by any logged-in role. | A database-backed design with aggregate thresholds and role scoping. Future roadmap. |
