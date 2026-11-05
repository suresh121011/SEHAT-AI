# P1 patient registry: linking visits by a patient-held code

> Implemented 2026-11-05 on branch `fix/p0-1-lab-parser`, after MO verification (`be7a477`). **Uncommitted, awaiting approval.**
> Research prototype; non-diagnostic. No urgency change, no new AI, no new personal data. The four WIP routers (CDS, referral, patient summary, surveillance) stay unregistered.

## 1. Design choice

A visit is linked to a patient **only by a SEHAT patient code the patient holds** (written on a slip), typed by the ANM and confirmed by a person. Nothing is matched automatically: there is no name, phone, Aadhaar or ABHA number, no fuzzy matching and no AI.

| Choice | Why |
|---|---|
| Patient-held code, not demographic matching | Matching on name or age is unreliable in this setting, and a wrong merge silently mixes two people's history. A shown code is explicit; a wrong link is visible and can be undone. |
| No new personal data | The registry stores a random code, the facility that issued it, and who did what. The data inventory (docs/11) gains no identifier. |
| Separate consent purpose `link_visits` | Linking shows earlier visits to staff. That is a new use, so the patient decides it per visit, independently of triage. |
| Offered in English only | Hindi and Odia wording is not written or reviewed (docs/13, entry C-link). The UI explains this; the server refuses the purpose in other languages. |

## 2. The code

Format `SP-XXXX-XXXX`: 7 random characters plus 1 check character, using the Crockford alphabet `0123456789ABCDEFGHJKMNPQRSTVWXYZ` (no I, L, O, U).

- **Check character:** weights 1, 3, 5, … 13 on the 7 characters, sum mod 32. Every single-character typo and most adjacent swaps fail the check, and the server answers `400 PATIENT_CODE_INVALID` ("one character is probably mistyped") *before* any lookup. A typo therefore never reaches someone else's record.
- **Normalisation:** upper-case; spaces and hyphens removed; O→0, I/L→1. The `SP` prefix is dropped only when all 10 characters are present, because a code body may itself start with S or P. The frontend (`lib/patientCode.ts`) applies the same rule.
- Generated with `secrets`; unique per database (`patients.patient_code UNIQUE`).

## 3. Consent

- `NOTICE_VERSION` is now `2026-11-05.1`. The English notice adds a paragraph: "Optional: linking your visits…" and the purpose "Also give me a SEHAT patient code and link my visits…".
- `link_visits` is a dependent purpose: it needs `triage`. Declining consent while including it returns 400. Asking for it with a Hindi or Odia notice returns `400 PURPOSE_NOT_AVAILABLE_IN_LANGUAGE`.
- Withdrawing `link_visits` (the "Stop showing this visit with other visits" button) is recorded as an append-only event. It **hides that visit from other visits' lists** straight away. The link row itself stays as history.
- The audit log records `consent_granted`, `consent_declined` and `consent_withdrawn` with the purpose enum only.

## 4. Data model (migration 14)

| Table | Contents | Rules |
|---|---|---|
| `patients` | `patient_id`, `patient_code`, `facility_code`, `created_by`, `created_by_role`, `created_at` | append-only (triggers block UPDATE and DELETE) |
| `patient_link_events` | `link` / `unlink`, `patient_id`, `case_id`, `basis` (`new_code` / `patient_code`), `reason_code` (`linked_in_error` / `patient_request`), `consent_seq` (the `link_visits` grant used), actor, role, time | append-only; a CHECK ties `basis` to `link` and `reason_code` to `unlink` |
| `consent_events` | rebuilt so the purpose CHECK includes `link_visits` | rows copied unchanged |

The current link for a visit is the latest event for that case. Migration 14 runs with foreign keys off, as steps 3 and 11 do. Tested on a temporary copy of the real database: 13 → 14, all 163 consent rows identical, `foreign_key_check` clean, append-only still enforced. The copy was deleted, and `sehat.db` itself was not touched.

## 5. Endpoints and access

All are under `/api/v1/cases/{case_id}/patient` (`routes/patients.py`, logic in `app/patient_registry.py`).

| Endpoint | Who | Checks |
|---|---|---|
| `GET` | ANM, medical officer (read access to the visit) | `load_case` read mode |
| `POST /new` (201) | the visit's ANM | `load_case` write, `link_visits` granted; `409 VISIT_ALREADY_LINKED` |
| `POST /lookup` | the visit's ANM | as above; returns the code, the number of visible visits and the first visit date, **nothing clinical** |
| `POST /link` (`confirm: true`) | the visit's ANM | as above; idempotent for the same patient |
| `POST /unlink` (`confirm: true`, `reason_code`) | the visit's ANM, or a medical officer with triage access | no consent needed to undo; `409 VISIT_NOT_LINKED` |

- **Scope:** a code issued in a facility outside the account's `ACCOUNT_FACILITIES` scope gets the **same 404 as an unknown code**, so a code reveals nothing about other facilities.
- **Other visits** are listed with date, facility and scenario only. A visit is listed only if the viewer passes `load_case` read access for it **and** that visit's own `link_visits` consent is still granted. Opening one goes through that visit's normal screens, consent and access checks.
- **Audit:** `patient_registered`, `patient_linked` and `patient_unlinked`, using the typed `PatientLinkDetails` (patient id, link event id, basis or reason enum). **The code itself is never written to the audit log.** A refusal for missing consent is recorded with `audit_denied`.
- **Triage is untouched:** no registry action writes a triage row, and nothing from another visit feeds the rules engine or the urgency.

## 6. User interface

- **Consent step** (`/intake/consent`): an English-only checkbox for `link_visits` (Hindi and Odia show a note instead), a four-purpose state grid, and the withdraw button. Once triage is granted, the ANM who created or handles the visit sees `PatientCodeCard`:
  - "Give the patient a new code": the code is shown with "Write … on a slip".
  - "Returning patient with a code slip?": a fixed `SP-` label sits outside the box. "Check code" shows the visible visit count, then "Yes, the slip matches — link this visit".
  - When linked, the card shows the code, its basis, the other visits, and "This code is not this patient's — unlink" with a confirmation dialog.
- **Case workspace** (medical officer): the same card, read-only for issuing. It shows the link and other visits (opening `/dashboard?case=…`) and allows unlinking.

## 7. Verification

| Check | Result |
|---|---|
| `tests/review/test_patient_registry.py` | 14 tests: typo detection, confusable characters, consent and its audit, refusal in hi/or, register then link, no identifiers or code in the audit log, a mistyped code, unknown and out-of-scope codes indistinguishable, one patient per visit, unlink history and append-only, withdrawal hides a visit, facility-filtered other visits, roles, no triage writes |
| Updated exact-state tests | `test_consent.py`, `test_schema_consent.py`, `test_migrations.py`, `test_redflag_keywords.py` (schema version 14); `test_facility.py` covers `GET /cases/{id}/patient` |
| Frontend | `src/lib/patientCode.test.ts` (4 tests): grouping, the prefix rule, completeness, error wording |
| Real app (`scripts/e2e_real_app_browser_check.py`) | 22/22 on a scratch database. Checks 16–22: the Hindi notice does not offer linking, a first visit gets a code, a mistyped code is caught before lookup, a return visit is linked after the ANM confirms, the medical officer sees the earlier visit (dates and facility only), a wrong link is undone without deleting anything, and no unexpected console errors (the typo's expected 400 is excluded). Screenshot `real_app_4_patient_code.png` reviewed. |

## 8. Limits and next steps

- **English only** until docs/13 entry C-link is written and reviewed.
- **A lost slip means visits stay unlinked.** The patient can be given a new code; joining two codes is not implemented, by design.
- **What the ANM sees of earlier visits** is limited by existing access: only visits they created or handle, in their facilities. A medical officer sees all linked visits in scope.
- **No longitudinal summary yet** (P1-3). Linked visits are listed, not combined; the encounter summary still describes one visit.
- Codes are not printed (no slip template), and no QR code is generated.
