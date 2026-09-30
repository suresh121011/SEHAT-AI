# SEHAT AI — API & Data Contracts

> **Version:** 1.0 | **Date:** September 2026
> **Architecture Source:** [SEHAT AI v5.0 Final Architecture](sehat_ai_final_architecture__2.md) — §12, §14, §16, §17
> **Stack:** FastAPI (Python) | SQLite (hackathon) | JWT auth

---

## 1. Base Configuration

```
Base URL:     http://localhost:8000/api/v1
Auth:         Bearer JWT
Content-Type: application/json
Headers:
  X-SEHAT-Role:    patient | anm | medical_officer | supervisor | admin
  X-SEHAT-User-ID: <uuid>
  X-SEHAT-Facility: <facility_code>
```

---

## 2. API Endpoints

### 2.1 Auth

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| POST | `/auth/login` | Login → JWT token | All |
| POST | `/auth/refresh` | Refresh token | All |

### 2.2 Consent

> **Implemented in Phase 3** — details, state rules and limitations in [`11_Privacy_Consent_Audit.md`](11_Privacy_Consent_Audit.md). Demo accounts only.

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| GET | `/consent/notice?language=en\|hi\|or` | Versioned notice text + review status (hi/or: unreviewed drafts) | any authenticated |
| POST | `/cases` | Create case `{scenario, facility_code}` → server `case_id` + opaque `patient_token` | patient, anm |
| GET | `/cases/{case_id}` | Case summary + effective consent per purpose | creator, medical_officer, supervisor |
| POST | `/cases/{case_id}/consent` | `{decision: grant\|decline, include_ai_assist, language, notice_version}`; method derived server-side | creator (patient, anm) |
| POST | `/cases/{case_id}/consent/withdraw` | `{purpose: triage\|ai_assist}`; withdrawing triage cascades to ai_assist | creator (patient, anm) |
| GET | `/cases/{case_id}/consent` | Consent history (actor role only) | creator, medical_officer, supervisor |
| POST | `/cases/{case_id}/triage` | `TriageInput`; requires `triage` consent; appends `triage_runs` | creating anm, medical_officer |
| DELETE | `/consent/{case_id}` | Data deletion — **deferred** (not implemented) | — |

Unauthorized and unknown case IDs return the same `404 NOT_FOUND`.

### 2.3 Intake

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| POST | `/cases/{case_id}/voice/transcriptions` | Submit a raw `audio/wav` clip for STT (Phase 4, [docs/12](12_Voice_Pipeline.md) §7) | patient, anm (case creator) |
| POST | `/cases/{case_id}/voice/candidates/{candidate_id}/readback` | Reviewer confirms/corrects/rejects a heard value | anm (creator), medical_officer |
| GET | `/cases/{case_id}/voice/prefill` | Reviewer-confirmed values shaped for `TriageInput` | anm (creator), medical_officer |
| POST | `/intake/document` | Upload document for OCR | patient, anm |
| POST | `/intake/image` | Upload medical image for MedGemma | patient, anm |
| POST | `/intake/body-map` | Submit body map selections | patient, anm |
| POST | `/intake/text` | Submit text/form input | patient, anm |
| POST | `/intake/vitals` | Submit vital signs | anm, medical_officer |

### 2.4 Triage

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| POST | `/triage/process` | Run full triage pipeline on collected intake | anm, medical_officer |
| GET | `/triage/{case_id}` | Get triage result for a case | medical_officer, supervisor |
| GET | `/triage/queue` | Get priority queue for facility | medical_officer, supervisor |
| PATCH | `/triage/{case_id}/sign-off` | Sign off on a triage note | medical_officer |
| PATCH | `/triage/{case_id}/override` | Override urgency (with reason) | medical_officer |

### 2.5 Referral

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| POST | `/referral` | Create referral packet | medical_officer |
| GET | `/referral/{referral_id}` | Get referral details | medical_officer, supervisor |
| GET | `/referral/active` | List active referrals for facility | medical_officer, supervisor |
| PATCH | `/referral/{referral_id}/status` | Update referral status | anm, medical_officer |
| GET | `/referral/overdue` | List overdue referrals (48h+ no update) | medical_officer, supervisor |

### 2.6 Audit

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| GET | `/audit/{case_id}` | Audit events for a case (`actor_role`, never `actor_id`) — implemented | supervisor |
| POST | `/audit/verify` | Recompute hash chain; returns `{ok, verified_through_seq, first_bad_seq}` — implemented | supervisor |
| GET | `/audit/governance` | Get governance telemetry (override rate, etc.) | supervisor, admin |

### 2.7 Export

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| GET | `/export/fhir/{case_id}` | Export case as FHIR R4 Bundle | medical_officer, supervisor |

---

## 3. JSON Schemas

### 3.1 ConsentEvent (implemented, append-only)

Consent is stored as an append-only history (`consent_events`); the effective state per purpose is the latest event. No audio or transcript is stored. See [`11_Privacy_Consent_Audit.md`](11_Privacy_Consent_Audit.md).

```json
{
  "seq": 12,
  "event_id": "uuid",
  "case_id": "uuid",
  "purpose": "triage",
  "action": "granted",
  "notice_version": "2026-09-30.1",
  "language": "or",
  "notice_review_status": "draft_unreviewed_translation",
  "method": "staff_attested_verbal",
  "actor_role": "anm",
  "created_at": "2026-09-30T10:15:00.000000+00:00"
}
```

`purpose` ∈ `triage | ai_assist | voice_cloud`; `action` ∈ `granted | declined | withdrawn`; `method` ∈ `patient_button | staff_attested_verbal | cascade_from_triage` (server-derived). `actor_id` is stored but not returned by read APIs.

### 3.2 Voice transcription (Phase 4 — implemented)

The sketch previously here (single `IntakePayload` with `stt_engine: "dakshini"`, `transcript_english`
and an audio `file_ref`) is superseded: no audio is stored, translation is deferred, and Dakshini could
not be verified. The implemented contract ([docs/12](12_Voice_Pipeline.md) §7):

```json
{
  "transcription_id": "uuid",
  "status": "completed",
  "language": "or",
  "engine": "local",
  "processing": "local inference (no provider call)",
  "model_id": "ai4bharat/indic-conformer-600m-multilingual@e9b71b369c04",
  "speech_segments": [[0.42, 6.1]],
  "transcript_raw": "…",
  "transcript_status": "machine transcript — not checked",
  "candidates": [
    {
      "candidate_id": "uuid",
      "field": "temp",
      "char_start": 31, "char_end": 41, "heard_text": "102 ଡିଗ୍ରୀ",
      "raw_value": 102, "unit": "f", "normalized": {"temp_c": 38.888888888888886},
      "flags": ["unit_inferred"],
      "readback_text": "ମୁଁ ଶୁଣିଲି ତାପମାତ୍ରା: 102 °F = 38.89 °C। ଏହା ଠିକ୍ କି?",
      "can_confirm": true,
      "resolution": null
    }
  ]
}
```

Prefill source reference (per value): `{"type": "voice_transcript" | "voice_manual_correction", "transcription_id", "candidate_id", "transcript_chars": [start, end], "clip_speech_sec": [start, end], "readback_outcome", "resolved_by_role"}`.

### 3.3 IntakePayload (Document OCR)

```json
{
  "case_id": "uuid",
  "input_type": "document",
  "document_type": "lab_report",
  "ocr_engine": "surya",
  "extracted_values": [
    {
      "field_name": "hemoglobin",
      "value": "8.5",
      "unit": "g/dL",
      "reference_range": "12.0-16.0",
      "out_of_range": true,
      "confidence": 0.94,
      "bbox": [120, 340, 280, 370],
      "snomed_code": "104142005"
    },
    {
      "field_name": "platelets",
      "value": "85000",
      "unit": "/μL",
      "reference_range": "150000-400000",
      "out_of_range": true,
      "confidence": 0.91,
      "bbox": [120, 380, 280, 410],
      "snomed_code": "61928009"
    }
  ],
  "godel_verification": {
    "overall_confidence": 0.89,
    "disputed_values": [],
    "rxnorm_matches": [],
    "reference_range_flags": ["hemoglobin_low", "platelets_below_reference_range"]
  },
  "source_ref": {
    "type": "ocr",
    "file_ref": "documents/PHC-2026-0453-lab.jpg"
  }
}
```

### 3.4 IntakePayload (Medical Image)

```json
{
  "case_id": "uuid",
  "input_type": "medical_image",
  "image_type": "ecg_strip",
  "analysis_engine": "medgemma",
  "findings": {
    "raw_description": "ST-segment elevation in leads V1-V4. Rate 110 bpm. Sinus tachycardia. No bundle branch block pattern.",
    "structured_fields": {
      "rate_bpm": "110",
      "rhythm": "sinus_tachycardia",
      "st_segment": "elevation V1-V4",
      "t_wave": "normal",
      "abnormalities": "ST elevation V1-V4"
    },
    "urgency_signals": [
      {
        "signal": "ST_ELEVATION",
        "action": "RED_FLAG",
        "note": "ST elevation detected — potential acute coronary event",
        "source": "MedGemma image analysis"
      }
    ],
    "confidence": 0.87,
    "disclaimer": "AI-described visual findings, pending specialist review"
  },
  "source_ref": {
    "type": "medical_image",
    "file_ref": "images/PHC-2026-0453-ecg.jpg"
  }
}
```

### 3.5 TriageNote (Core Output)

```json
{
  "case_id": "uuid",
  "patient_token": "PHC-2026-0453",
  "facility_code": "PHC-KHURDA-01",
  "scenario": "opd_triage",
  "created_at": "2026-09-29T10:15:45+05:30",

  "urgency": {
    "level": "RED",
    "rules_level": "RED",
    "jev_level": "YELLOW",
    "llm_suggested_level": "RED",
    "final_level": "RED",
    "method": "max(rules, jev, llm)"
  },

  "red_flags": [
    {
      "rule_name": "ATP_RED_SEVERE_PAIN",
      "check": "Time-sensitive: severe pain anywhere in body",
      "source": "ATP_2022 — AIIMS Triage Protocol, J Emerg Trauma Shock 2022, Supplementary Table 1",
      "triggered_by": {
        "red_flag": {"value": "severe_pain", "source_ref": "anm_red_flag_screen + transcript:0:10-0:18"}
      }
    }
  ],

  "scores": {
    "news2": {"score": 2, "interpretation": "low (RCP Chart 2)", "source": "RCP NEWS2"},
    "qsofa": {"score": 0, "interpretation": "negative screen (not a diagnosis)", "source": "Sepsis-3"},
    "jev_confidence": 0.82
  },

  "fields": [
    {
      "field_name": "chief_complaint",
      "value": "Fever for 3 days, 102°F, severe headache, abdominal pain",
      "source_type": "transcript",
      "source_ref": {"type": "audio", "start_sec": 4, "end_sec": 18},
      "confidence": 0.94,
      "snomed_code": "386661006",
      "status": "confirmed"
    },
    {
      "field_name": "platelets",
      "value": "85000",
      "unit": "/μL",
      "source_type": "ocr",
      "source_ref": {"type": "ocr", "bbox": [120, 380, 280, 410]},
      "confidence": 0.91,
      "snomed_code": "61928009",
      "status": "confirmed",
      "maker_voting": {"pass_1": "85000", "pass_2": "85000", "pass_3": "85000", "agreement": true}
    }
  ],

  "missing_fields": [
    {"field": "tourniquet_test", "reason": "Clinician prompt (NCVBDC 2023 triage parameter) — not a rules-engine input"},
    {"field": "fluid_io", "reason": "Clinician prompt — not a rules-engine input"}
  ],

  "counterfactual": [
    {"if_changed": "severe_pain not recorded (vitals unchanged)", "then_urgency": "GREEN — current engine; dengue warning-sign pack deferred (docs/10 ADR-7)"},
    {"if_changed": "red-flag screen not completed", "then_urgency": "YELLOW + needs_human_review (safety floor)"},
    {"if_changed": "SpO2 < 90%", "then_urgency": "RED (unchanged; ATP_RED_SPO2)"}
  ],

  "ai_summary": "28-year-old patient presenting with 3-day history of high-grade fever (102°F), severe headache, and abdominal pain. Lab report shows platelets 85,000/μL (below reference range) for clinician review. Urgency RED set by rules engine (ATP_RED_SEVERE_PAIN).",
  "ai_disclaimer": "AI-drafted triage summary. Pending review by qualified medical officer. Not a diagnosis.",

  "review": {
    "status": "pending",
    "reviewer_id": null,
    "reviewer_name": null,
    "signed_off_at": null,
    "edits": [],
    "override": null
  }
}
```

### 3.5a TriageProcess — `POST /triage/process` (implemented, Phase 2)

Deterministic rules engine, no LLM. Full field reference, rule list and sources: [`10_Safety_Rules_Engine.md`](10_Safety_Rules_Engine.md). Scenarios: `opd | maternal | chronic_ncd | health_camp | campus_fever | occupational | referral`. Scenario-specific data goes in a block named after the scenario (e.g. `"maternal": {"danger_sign_screen_completed": true, "danger_signs": [...], "hb_g_dl": 9.8}`). Impossible values return `400 VALIDATION_ERROR`.

**Request**
```json
{
  "scenario": "opd",
  "age_years": 28,
  "red_flag_screen_completed": true,
  "red_flags_present": ["sudden_abdominal_pain"],
  "suspected_infection": true,
  "vitals": {
    "resp_rate": 22, "spo2": 96, "on_supplemental_oxygen": false, "pulse": 104,
    "sbp": 108, "dbp": 70, "temp_c": 38.9, "consciousness": "A"
  }
}
```

**Response** (abridged)
```json
{
  "urgency": "RED",
  "determination": "complete",
  "needs_human_review": false,
  "scenario": "opd",
  "triggered_rules": [
    {
      "rule_id": "ATP_RED_SUDDEN_ABDOMINAL_PAIN", "family": "atp", "urgency": "RED",
      "reason": "Time-sensitive: sudden onset abdominal pain",
      "source_id": "ATP_2022", "source": "Singh SK, Sahu AK, et al. … J Emerg Trauma Shock 2022;15(3):124-7, Supplementary Table 1",
      "evidence": {"red_flag": {"value": "sudden_abdominal_pain", "threshold": "present on assessment"}}
    },
    {
      "rule_id": "NEWS2_MEDIUM", "family": "news2", "urgency": "YELLOW",
      "source_id": "RCP_NEWS2_2017",
      "evidence": {"news2": {"value": 5, "threshold": "5-6 (RCP medium clinical risk)"}}
    }
  ],
  "scores": {
    "news2": {"status": "complete", "total": 5, "band": "medium", "components": {"resp_rate": {"value": 22, "points": 2}, "…": "…"}},
    "qsofa": {"status": "complete", "total": 1, "positive": false, "note": "qSOFA is a risk prompt, not a diagnosis of sepsis (SEPSIS3_2016)"}
  },
  "missing_fields": [],
  "advisories": [],
  "engine_version": "1.0.0",
  "ruleset_version": "1.0.0",
  "evaluated_at": "2026-09-30T10:00:00Z"
}
```

When data is incomplete, `urgency` is never `GREEN`: it is at least `YELLOW` with `"determination": "insufficient_data"`, `"needs_human_review": true` and the list in `missing_fields`.

**Mapping to TriageNote (3.5):** `urgency` → `urgency.rules_level`; `triggered_rules` → `red_flags` (`rule_id` ≈ `rule_name`, `evidence` ≈ `triggered_by`); `scores` → `scores`. The LLM/JEV suggestion is combined with `enforce_raise_only()` → `urgency.final_level`. The 3.5 example uses the demo patient (docs/07); platelets appear as an OCR field for the clinician, not as a rule input — no engine rule reads platelets and the dengue pack is deferred (docs/10 ADR-7).

### 3.6 ReviewSignOff

```json
{
  "case_id": "uuid",
  "reviewer_id": "DR-PATEL-001",
  "reviewer_name": "Dr. Rajesh Patel",
  "action": "approve",
  "edits": [
    {
      "field": "chief_complaint",
      "old_value": "Fever for 3 days, 102°F",
      "new_value": "Fever for 3 days, 102°F, with rigors",
      "reason": "Patient mentioned rigors on examination"
    }
  ],
  "override": null,
  "signed_off_at": "2026-09-29T10:17:30+05:30"
}
```

### 3.7 UrgencyOverride

```json
{
  "case_id": "uuid",
  "reviewer_id": "DR-PATEL-001",
  "old_urgency": "RED",
  "new_urgency": "YELLOW",
  "reason_code": "clinical_reassessment",
  "reason_text": "On examination abdominal pain is mild, not severe; vitals stable",
  "timestamp": "2026-09-29T10:20:00+05:30"
}
```

### 3.8 ReferralPacket

```json
{
  "referral_id": "uuid",
  "case_id": "uuid",
  "patient_token": "PHC-2026-0453",
  "from_facility": "PHC-KHURDA-01",
  "to_facility": "DH-BBSR-01",
  "urgency": "RED",
  "flags": ["ATP_RED_SEVERE_PAIN"],
  "triage_note_ref": "uuid (link to TriageNote)",
  "transport_plan": {
    "mode": "108_ambulance",
    "escort_required": true,
    "estimated_travel_min": 45
  },
  "status": "referred",
  "status_history": [
    {"status": "referred", "timestamp": "2026-09-29T10:18:00+05:30", "updated_by": "DR-PATEL-001"},
    {"status": "in_transit", "timestamp": "2026-09-29T10:25:00+05:30", "updated_by": "ANM-KHURDA-03"}
  ],
  "overdue": false,
  "overdue_threshold_hours": 48,
  "created_at": "2026-09-29T10:18:00+05:30"
}
```

### 3.9 AuditEvent

> **Implemented as `audit_log`** ([`11_Privacy_Consent_Audit.md`](11_Privacy_Consent_Audit.md)): fields `seq, event_id, timestamp, actor_id, actor_role, action, case_id, outcome, request_id, details_json, previous_hash, current_hash`. Details are typed per action (counts, rule IDs, versions — no raw clinical content). Tamper-evident, not immutable. The example below is the original design sketch.

```json
{
  "event_id": "uuid",
  "timestamp": "2026-09-29T10:17:30+05:30",
  "actor_id": "DR-PATEL-001",
  "actor_role": "medical_officer",
  "action": "triage_signed_off",
  "case_id": "uuid",
  "details": {
    "urgency": "RED",
    "edits_count": 1,
    "override": false,
    "review_time_sec": 105
  },
  "previous_hash": "a3f2b7c8d9e0...",
  "current_hash": "b4c3d8e9f0a1..."
}
```

---

## 4. FHIR R4 Export Shape

For interoperability with ABDM and TriZetto, cases export as FHIR R4 Bundles:

```json
{
  "resourceType": "Bundle",
  "type": "document",
  "entry": [
    {
      "resource": {
        "resourceType": "Composition",
        "status": "final",
        "type": {
          "coding": [{
            "system": "http://loinc.org",
            "code": "11488-4",
            "display": "Consultation note"
          }]
        },
        "title": "SEHAT AI Triage Note",
        "date": "2026-09-29T10:17:30+05:30",
        "author": [{"display": "SEHAT AI (AI-assisted) + Dr. Rajesh Patel (reviewer)"}],
        "section": [
          {
            "title": "Chief Complaint",
            "text": {"div": "<div>Fever for 3 days, 102°F, severe headache</div>"}
          },
          {
            "title": "Triage Assessment",
            "text": {"div": "<div>RED — ATP_RED_SEVERE_PAIN: severe pain anywhere in body (AIIMS Triage Protocol 2022)</div>"}
          }
        ]
      }
    },
    {
      "resource": {
        "resourceType": "Observation",
        "status": "final",
        "code": {
          "coding": [{
            "system": "http://snomed.info/sct",
            "code": "61928009",
            "display": "Platelet count"
          }]
        },
        "valueQuantity": {
          "value": 85000,
          "unit": "/uL",
          "system": "http://unitsofmeasure.org"
        },
        "interpretation": [{
          "coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": "L",
            "display": "Low"
          }]
        }]
      }
    },
    {
      "resource": {
        "resourceType": "RiskAssessment",
        "status": "final",
        "prediction": [{
          "qualitativeRisk": {
            "coding": [{
              "system": "http://sehat-ai/triage",
              "code": "RED",
              "display": "Immediate — ATP time-sensitive: severe pain"
            }]
          }
        }],
        "basis": [
          {"display": "AIIMS Triage Protocol 2022, Supplementary Table 1 (doi:10.4103/jets.jets_146_21)"}
        ]
      }
    }
  ]
}
```

---

## 5. Error Response Format

All API errors follow a consistent shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Missing required field: consent_id",
    "details": {
      "field": "consent_id",
      "constraint": "Consent must be recorded before intake"
    },
    "request_id": "uuid"
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|:---|:---:|:---|
| `VALIDATION_ERROR` | 400 | Request validation failed |
| `CONSENT_REQUIRED` | 403 | Consent for this purpose is not in effect |
| `CONSENT_WITHDRAWN` | 409 | Consent changed while an AI request was in progress; output discarded |
| `NOTICE_VERSION_STALE` | 409 | Consent notice changed; re-review required |
| `NOTHING_TO_WITHDRAW` | 409 | No consent in effect for that purpose |
| `SCENARIO_MISMATCH` | 409 | Triage scenario differs from the case scenario |
| `AI_INPUT_UNSUPPORTED_LANGUAGE` | 422 | AI assistance accepts English text only (prototype) |
| `AI_ADAPTER_ERROR` | 502 | AI adapter failed; no output returned |
| `PII_REDACTION_UNAVAILABLE` | 503 | Redaction unavailable; request blocked (fail closed) |
| `INTERNAL_ERROR` | 500 | Generic error; no request values echoed |
| `UNAUTHORIZED` | 401 | Invalid or expired JWT |
| `FORBIDDEN` | 403 | Role does not have permission |
| `NOT_FOUND` | 404 | Resource not found |
| `TRIAGE_LOCKED` | 409 | Case already signed off, cannot modify |
| `PII_DETECTED` | 422 | Possible identifier remained after redaction; request blocked |
| `INJECTION_BLOCKED` | 422 | Prompt injection pattern detected in input |

---

## 6. WebSocket Events (Real-time)

For the reviewer dashboard real-time queue updates:

```
ws://localhost:8000/ws/queue/{facility_code}
```

| Event | Payload | Trigger |
|:---|:---|:---|
| `new_case` | `{case_id, urgency, summary}` | New triage note enters queue |
| `urgency_changed` | `{case_id, old, new, reason}` | Override or re-assessment |
| `escalation` | `{case_id, timer_elapsed_sec}` | RED case > 3 min unacknowledged |
| `signed_off` | `{case_id, reviewer_id}` | Case reviewed and approved |
| `referral_update` | `{referral_id, new_status}` | Referral status changed |

---

> **Related Documents:**
> - [Technical Architecture](03_Technical_Architecture.md) — Pipeline that feeds these endpoints
> - [Frontend Specification](05_Frontend_Specification.md) — UI screens that consume these APIs
> - [Security & Privacy](04_Security_Privacy_Access.md) — Auth, RBAC, and audit detail
