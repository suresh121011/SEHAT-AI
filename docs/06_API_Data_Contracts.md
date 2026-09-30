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

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| POST | `/consent` | Record patient consent | patient, anm |
| GET | `/consent/{case_id}` | Get consent record for a case | medical_officer, admin |
| DELETE | `/consent/{case_id}` | Revoke consent (triggers data deletion) | patient |

### 2.3 Intake

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| POST | `/intake/voice` | Submit voice recording for STT | patient, anm |
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
| GET | `/audit/{case_id}` | Get audit trail for a case | medical_officer (own), supervisor, admin |
| GET | `/audit/governance` | Get governance telemetry (override rate, etc.) | supervisor, admin |

### 2.7 Export

| Method | Endpoint | Description | Roles |
|:---:|:---|:---|:---|
| GET | `/export/fhir/{case_id}` | Export case as FHIR R4 Bundle | medical_officer, supervisor |

---

## 3. JSON Schemas

### 3.1 ConsentRecord

```json
{
  "consent_id": "uuid",
  "case_id": "uuid",
  "patient_token": "PHC-2026-0453",
  "consent_type": "data_collection_and_triage",
  "method": "audio",
  "language": "or",
  "audio_ref": "s3://consent/PHC-2026-0453.wav",
  "emergency_bypass": false,
  "granted_at": "2026-09-29T10:15:00+05:30",
  "revoked_at": null,
  "facility_code": "PHC-KHURDA-01"
}
```

### 3.2 IntakePayload (Voice)

```json
{
  "case_id": "uuid",
  "input_type": "voice",
  "language_detected": "or",
  "audio_duration_sec": 18.5,
  "stt_engine": "dakshini",
  "transcript_raw": "ତିନି ଦିନ ହେଲା ଜ୍ୱର ହେଉଛି...",
  "transcript_english": "Fever for 3 days, temperature 102°F, severe headache",
  "read_back_confirmed": true,
  "source_ref": {
    "type": "audio",
    "start_sec": 4,
    "end_sec": 18,
    "file_ref": "audio/PHC-2026-0453.wav"
  }
}
```

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
    "reference_range_flags": ["hemoglobin_low", "platelets_critical"]
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
      "rule_name": "dengue_warning_signs",
      "check": "platelets < 100K AND fever >= 3 days",
      "source": "WHO Dengue Classification 2009 + AIIMS Protocol",
      "triggered_by": {
        "platelets": {"value": 85000, "source_ref": "ocr:lab_report:L380"},
        "fever_duration": {"value": 3, "source_ref": "transcript:0:04-0:08"}
      }
    }
  ],

  "scores": {
    "news2": {"score": 4, "interpretation": "Increased observation", "source": "RCP NEWS2"},
    "qsofa": null,
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
    {"field": "tourniquet_test", "reason": "Required for dengue assessment"},
    {"field": "fluid_io", "reason": "Required for dengue warning signs"}
  ],

  "counterfactual": [
    {"if_changed": "platelets > 100K", "then_urgency": "YELLOW"},
    {"if_changed": "no fever", "then_urgency": "YELLOW"},
    {"if_changed": "SpO2 < 94%", "then_urgency": "RED (unchanged)"}
  ],

  "ai_summary": "28-year-old patient presenting with 3-day history of high-grade fever (102°F), severe headache, and abdominal pain. Laboratory findings show thrombocytopenia (platelets 85,000/μL). Clinical picture consistent with dengue warning signs per WHO classification.",
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
  "reason_text": "Repeat platelet count at 1.1 lakh after hydration",
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
  "flags": ["dengue_warning_signs", "thrombocytopenia"],
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
            "text": {"div": "<div>RED — Dengue warning signs (WHO 2009 + AIIMS Protocol)</div>"}
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
              "display": "Immediate — Dengue Warning Signs"
            }]
          }
        }],
        "basis": [
          {"display": "AIIMS Triage Protocol (PubMed 36353399)"},
          {"display": "WHO Dengue Classification 2009"}
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
| `CONSENT_REQUIRED` | 403 | No consent record found for case |
| `UNAUTHORIZED` | 401 | Invalid or expired JWT |
| `FORBIDDEN` | 403 | Role does not have permission |
| `NOT_FOUND` | 404 | Resource not found |
| `TRIAGE_LOCKED` | 409 | Case already signed off, cannot modify |
| `PII_DETECTED` | 422 | PII detected in field that should be redacted |
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
