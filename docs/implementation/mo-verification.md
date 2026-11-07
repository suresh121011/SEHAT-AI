# Medical officer verification of document values

> Implemented 2026-11-04 on branch `fix/p0-1-lab-parser`, after H0-1 (`d4dbbb6`). **Uncommitted, awaiting approval.**
> Research prototype; non-diagnostic. No backend change, no urgency change, no new AI.

## 1. Authorization model (unchanged; now proven by tests)

The medical officer uses the **same endpoints** as the ANM:
- `GET /cases/{id}/verification-queue`
- `GET /cases/{id}/documents`
- `.../pages/{n}/image`
- `POST .../documents/{doc}/attestation`
- `POST .../documents/fields/{field}/review`

Access is decided on the server for every request:

| Rule | Where | Effect |
|---|---|---|
| **Role** | `_reviewers = require_roles(ANM, MEDICAL_OFFICER)` (`routes/ocr.py`, `routes/ai.py`) | Patient and supervisor get 403. With no session, the response is 401. |
| **Case and facility** | `consent.load_case(..., "triage")`: the creating or handling ANM, or any medical officer, and only inside the account's facility scope from the trusted `ACCOUNT_FACILITIES` mapping. | Another facility's case returns the **same 404 as a missing case**, so it reveals nothing. |
| **Consent** | `consent.require(case, "triage")` | Without triage consent, nothing is served or recorded. |
| **ID binding** | Queries match `field_id = ? AND case_id = ?` and `document_id = ? AND case_id = ?` | A field or document from another case returns 404 even when used through an allowed case. |
| **Provenance and audit** | Append-only `ocr_review_events` and `ocr_attestation_events` with `actor_role`; audit actions `ocr_review_resolved` and `ocr_attestation_recorded` | A medical officer's decisions are recorded exactly like an ANM's, under `medical_officer`. Corrections are a separate `corrected_json`; the machine reading is never overwritten. |

## 2. User interface (one review experience)

**Shared code, moved unchanged out of `app/intake/documents/page.tsx`:**
- `components/review/DocumentChecks.tsx`: the patient question, the row counts, `FieldRow`, `ReviewPanel` (confirm, correct, not sure, reject; keys C/U/R) and the page viewer with the source region.
- `lib/useDocumentVerification.ts`: the documents, the "What to check, in order" queue, auto-advance, and the `?field=` / `?doc=` deep links.
- `lib/documentReview.ts`: the types.

**The ANM's documents step** (`/intake/documents`) uses these. Its behaviour is unchanged; upload, delete, images and "Reviewed values" stay on that page.

**The new `/dashboard/verify?case=…` page** is for the medical officer only, enforced by a new `ROUTE_ACCESS` rule. It uses the same queue panel and the same review components. It has no upload and no delete. Voice items are listed but not linked, because read-back is the ANM's step.

**Entry points:**
- The case workspace has a "Check document values" button.
- The encounter summary's "Open in verification" links now point to the viewer's own page: `verificationBase(role)` gives the ANM `/intake/documents` and the medical officer `/dashboard/verify`.

**What stays separate:** OCR engine agreement stays a machine label, a person's check stays a separate event, and the clinician's summary review stays a separate step. None of them changes urgency.

## 3. Verification

**Tests:**

| Suite | Result |
|---|---|
| Backend, full | **1475 passed, 0 failed** (6 new in `tests/review/test_mo_verification.py`) |
| Frontend | **114/114** (route rule, role-aware links); `tsc` and ESLint clean |

The 6 backend tests cover:
- a medical officer in the facility can see the queue and match and decide documents (confirmed, unsure, corrected);
- the role is recorded on review events and audit rows, and corrections are kept separate from the unchanged machine reading;
- an ANM and a medical officer see one shared queue;
- another facility's case and a missing case get the same 404 on 5 endpoints, with nothing written;
- ID substitution is refused;
- patient and supervisor get 403, with no session 401;
- no triage run or review event is written.

**Real app:** `scripts/e2e_real_app_browser_check.py` passed 16/16 with real OCR, a scratch database and headless Chrome.
- The medical officer's links go to `/dashboard/verify`.
- Their key decision was stored with `actor_role = medical_officer`.
- Auto-advance worked, and there was no upload or delete.
- An ANM opening `/dashboard/verify` was redirected to `/intake`.

## 4. Limits

- **The patient question assumes a face-to-face visit.** It reads "Is this report for the patient in front of you, for this visit?", which may not hold for a medical officer reviewing remotely. The wording is shared and unchanged; changing it needs the docs/13 language review.
- **No React component tests.** The repository has no component-test harness: frontend tests cover view logic only, and the screens are covered by the real-app script.
- **Any medical officer in the facility may verify any of its cases.** This follows the existing `load_case` rule for medical officers. There is no per-case assignment.
