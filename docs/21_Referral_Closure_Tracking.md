# 21. Referral closure tracking

> **Status:** built in Phase 2 (features #26–29; architecture §16; contract docs/06 §2.5, §3.8, §4). Synthetic data
> only. SEHAT AI is a research prototype, not a clinically validated device.

## 1. What it does

A medical officer refers a patient from a **signed-off** triage to a higher facility and records each step until the
referral is closed (the API also accepts steps from the visit's own health worker; the screen is the medical officer's):

```
referred ──► on the way ──► reached ──► seen ──► outcome recorded (admitted · treated and sent home · referred onward)
   │              │
   └──────────────┴──► did not reach (no transport · cost · no one to go with the patient · patient chose not to go ·
                        could not contact the patient)
```

- **Overdue:** no update for **48 hours** while the referral is *referred* or *on the way* (architecture §16). Once the
  patient has reached the facility, the closure gap is closed and the flag no longer applies. Overdue is computed
  when a list is read; SEHAT sends no SMS or alert. The tracking page is where an overdue referral shows.
- **Letter:** a printable referral note built only from the stored referral and its signed-off triage run: urgency
  from the rules engine (raised, never lowered, by a reviewer override), the rules that fired with their cited
  sources and evidence, the recorded vital signs, what was not recorded, the reason, transport, escort and insurance,
  and the sign-off time. The patient is identified by the SEHAT token only.
- **FHIR R4 export:** a `document` Bundle of the same content (§4). Structure-checked by tests; not run through an
  official validator or the ABDM profiles.
- **Tracking page** (`/dashboard/referrals`): open referrals in the account's facilities, overdue first, with counts.

## 2. Safety controls

| Rule | How |
|:---|:---|
| Only a medical officer refers, and only from a signed-off triage | Role checked at the route and in the service; the triage run must be the case's latest and have a sign-off event, or 409 `SIGN_OFF_REQUIRED` / `STALE_TRIAGE_RUN` |
| Rules-first urgency | The packet stores the review queue's priority urgency for that run: the rules-engine urgency, raised (never lowered) by an override. Nothing here evaluates or writes urgency or a triage run |
| Case access and facility scope | `consent.load_case` before anything else; another facility's case, a missing case and another case's referral id are the same 404 (`tests/review/test_facility.py` probes all five case routes) |
| Consent | Triage consent must be in effect inside the write transaction; a refusal is audited (`consent_denied`) |
| No free text | Every request field is a closed-list code, a UUID, a boolean or a bounded number (`tests/privacy/test_boundary.py`) |
| Append-only, forward-only | `referral_packets` and `referral_events` reject UPDATE and DELETE (triggers); each status at most once per referral; the caller sends the status it saw (`expected_status`), or 409 `STALE_REFERRAL_STATUS` |
| Audit | `referral_created`, `referral_status_recorded`, `referral_exported`: ids, urgency, destination and steps only. Reason, transport, insurance and outcome codes stay in the referral tables, because supervisors read the audit log |
| Supervisors see metadata only | Tracking lists and case views without reason, transport, insurance, outcome codes, vitals or the letter (docs/11, owner decision D-B) |
| No diagnosis | Labels and letter text are checked against `app.ai.guard` in the tests; the fixed disclaimer says the urgency is not a diagnosis |

## 3. API (implemented)

| Method | Path | Roles | Notes |
|:---|:---|:---|:---|
| GET | `/cases/{case_id}/referrals` | ANM (creator or handler), medical officer, supervisor | Referrals of the case; `can_refer` and `refer_blocked`; options for clinical viewers |
| POST | `/cases/{case_id}/referrals` | medical officer | `{triage_run_id, to_facility, reason_code, transport_mode, escort_required, travel_minutes?, insurance_scheme, confirm: true}` → 201 |
| POST | `/cases/{case_id}/referrals/{referral_id}/status` | ANM (creator or handler), medical officer | `{status, expected_status, detail_code?, confirm: true}`; `detail_code` only for `outcome_recorded` and `not_reached` |
| GET | `/cases/{case_id}/referrals/{referral_id}/letter` | ANM (creator or handler), medical officer | Structured letter; the UI prints it |
| GET | `/cases/{case_id}/referrals/{referral_id}/fhir` | medical officer | FHIR R4 document Bundle; audited |
| GET | `/referral/active` | medical officer, supervisor | Open referrals in scope, overdue first, with counts |
| GET | `/referral/overdue` | medical officer, supervisor | Overdue ones only |

The contract sketch in docs/06 put creation at `POST /referral`. Case-scoped paths were used instead so every referral
route goes through the same case-access check as the rest of the API.

## 4. FHIR R4 bundle

`Bundle.type = document`; resource ids are UUIDv5 from the referral id, so a referral always exports with the same ids (statuses change as it moves on).

| Resource | Content |
|:---|:---|
| Composition (first) | LOINC 57133-1 "Referral note", status final, sections: reason, triage urgency (rules engine), vital signs, transport; attester (professional): the signing medical officer's role and time |
| Patient | Identifier `urn:sehat:patient-token` only: no name, contact or address |
| Organization × 2 | Referring and receiving facility (code and name) |
| Encounter | The PHC visit (class AMB) |
| ServiceRequest | SNOMED CT 3457005 "Patient referral", intent order, priority from urgency (RED → stat, YELLOW → urgent, GREEN → routine), performer = receiving facility, status active / completed (outcome recorded) / revoked (did not reach) |
| Observation | Recorded vitals only: LOINC 9279-1, 59408-5, 8867-4, 8310-5, and the 85354-9 blood-pressure panel (8480-6, 8462-4), UCUM units |
| RiskAssessment | Urgency code (`urn:sehat:triage-urgency`), the method (engine and ruleset versions) and the cited sources as basis |

## 5. Data

Migration step 19 adds `referral_packets` (one row per referral, fixed when made) and `referral_events` (one row per
status). The baseline `referrals` table is not used or changed.

## 6. Decisions and limits

- **Consent:** referral actions require triage consent; the notice says triage data is shared "with the health worker
  and doctor treating me", which covers the receiving doctor. A separate referral purpose would change the consent
  notice and need Hindi and Odia review: an **owner decision**, not made here.
- **Facility list:** a small demo directory of synthetic codes with generic names (`app/referral_notes/options.py`).
  Replace with the state's facility registry (for example the ABDM Health Facility Registry) before real use.
- **Insurance:** recorded as stated by the patient; SEHAT does not check eligibility.
- **No calling or notification:** SEHAT does not call 108 or notify the receiving facility; staff do.
- **Receiving side:** the receiving facility does not use SEHAT in this prototype; updates are recorded by the
  referring side (for example after a phone call). The follow-up-call and escalation steps of architecture §16 are not
  built.
- **Urgency wording:** a raised urgency is shown as "raised by a reviewer from YELLOW"; a lower value a reviewer recorded is
  shown beside the referral's urgency, which keeps the rules urgency.
- **Reviewer identity:** the letter names the role and time of sign-off, not the account, as everywhere in the review
  screens (the demo accounts are shared).

## 7. Verification

- `backend/tests/referral_notes/`: 41 tests (pure rules, API, FHIR structure), required modules in the CI skip gate.
- `tests/review/test_facility.py` probes the five new case routes for facility isolation.
- `frontend/src/lib/referral.test.ts`: 7 tests for the form checks and wording.
- Real app: checked on scratch servers (backend 8010, frontend 3010, scratch database); see the Phase 2 pull request.
