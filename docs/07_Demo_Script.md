# SEHAT AI — Demo Script (5 Minutes)

> **Version:** 1.1 | **Date:** 2026-09-30 (aligned to the Phase 2 rules engine — see [`10_Safety_Rules_Engine.md`](10_Safety_Rules_Engine.md))
> **Architecture Source:** [SEHAT AI v5.0 Final Architecture](sehat_ai_final_architecture__2.md) — §25
> **Setting:** Odisha PHC — febrile illness with severe abdominal pain (dengue-like presentation as clinical context only)
> **Status:** Research prototype, not a clinically validated device. The demo patient is a **synthetic, constructed teaching case**.

> **What decides urgency:** the deterministic Phase 2 rules engine (`POST /api/v1/triage/process`). Every urgency shown in this demo must match a `rule_id` the engine actually returns. SEHAT AI does not diagnose dengue: a dengue warning-sign pack is **deferred** until it can be built from verified NCVBDC 2023 / WHO criteria (docs/10 ADR-7), and **no engine rule reads platelet count**.

---

## Demo Overview

| Item | Detail |
|:---|:---|
| **Duration** | 5 minutes |
| **Scenario** | 28-year-old patient at PHC Khurda: 3 days of fever, headache, severe abdominal pain |
| **Languages** | Hindi voice input verified on-device; Odia voice input not yet verified; translation to English deferred (Phase 6) |
| **Modalities** | Voice + lab report (OCR) + body map |
| **Story** | Intake → Triage → Review → Sign-off → Referral → Closure Tracking |

### Evaluation Beats Covered

| Criterion | Weight | Beat(s) |
|:---|:---:|:---|
| Safety-first triage | 20% | Cited ATP rule fires RED, rule evidence + threshold, YELLOW safety floor, raise-only LLM boundary |
| Extraction & summarisation | 20% | Source-linked note, OCR extraction, MAKER voting |
| Multimodal capability | 15% | Voice (Odia), OCR (lab report), body map |
| India-wide facility relevance | 15% | Odia language, PHC scenario, scenario rule pack |
| Human-review & escalation | 15% | Priority queue, sign-off, override, escalation timer |
| Privacy & responsible AI | 10% | Consent, PII redaction, non-diagnostic, audit |
| Demo quality | 5% | Smooth end-to-end story |

---

## Pre-Demo Checklist

- [ ] Backend running (`cd backend && ../.venv/bin/uvicorn app.main:app --reload`)
- [ ] Frontend running (`npm run dev`)
- [ ] Demo accounts seeded (patient, ANM, MO)
- [ ] Synthetic lab report image ready (CBC with Platelets 85K, Hb 11.2 — context for the reviewer, not a rule input)
- [ ] Check whether this device has an Odia voice for read-aloud (browser voices vary); if not, the screen says read-aloud is unavailable — read the notice to the patient
- [ ] Microphone tested for voice recording
- [ ] Reviewer dashboard empty queue (fresh start)
- [ ] Audit log table empty
- [ ] Browser at 1280×800 resolution

---

## Script

### Beat 1: Setup & Consent (0:00–0:45)

**[Screen: Login → Intake Home]**

> **Narrator:** *"Meet SEHAT AI. It's not an AI doctor — it's a rules-first triage assistant that organises patient symptoms for doctors. Let's see it work at a Primary Health Centre in Khurda, Odisha."*

**Actions:**
1. Login as ANM (health worker)
2. Select facility: "PHC Khurda, Odisha"
3. Select scenario: "OPD Triage"
4. Consent screen appears — in **Odia**
5. Click **"Read Aloud"** → TTS reads consent in Odia
6. Patient says **"haan"**; the ANM ticks the attestation box and records the agreement (no audio stored — docs/11)

> **Talking Point:** *"Consent is layered — read aloud in the patient's language where a matching voice exists, and the ANM attests the patient's verbal agreement (no audio stored). DPDP-ready by design — built around DPDP Act Section 6 consent ahead of substantive duties starting May 2027."*

**Eval Hits:** Privacy (10%) — consent flow. India (15%) — Odia language.

---

### Beat 2: Voice Input + Read-Back (0:45–1:45)

**[Screen: Voice Recorder]**

> **Narrator:** *"Speech recognisers often get numbers wrong — and triage depends on numbers — so nothing heard is used until the health worker checks it."*

**Actions.** Details and per-language status are in [docs/12](12_Voice_Pipeline.md). The **verified** path is Hindi on the on-device model; the Odia paths are **not yet verified**, so present them as such.

1. Choose **हिन्दी** and **On this device (local model)**. The page shows which engine ran, and that it was checked on a synthetic clip only.
2. Click **▶ Sample: Hindi fever (synthetic)**, or record live. Live recording needs localhost or HTTPS.
   - The screen shows **Raw transcript (not checked)**: *"मुझे तीन दिन से बुखार है तापमान एक सौ दो डिग्री है"*, with the heard values highlighted.
3. The read-back card says *"मैंने सुना तापमान: 102 °F = 38.89 °C। क्या यह सही है?"*. The Hindi wording is an unreviewed draft, and a banner says so. 🔊 plays it only if a matching voice exists; otherwise the screen says spoken read-back is unavailable.
   - 102 °F is exactly 38.888… °C, and the engine uses the unrounded value. It is *not* above the ATP fever threshold (>39 °C), so fever alone does not make this patient RED.
4. The **ANM**, not the patient, taps **✓ Yes, that's right**. The value appears under *Values the health worker confirmed*, labelled "heard, confirmed by anm".
5. **Safety beat.** Click **▶ Sample: Hindi 'saadhe 39'**. The model hears *"तापमान साढ़े उनतालीस डिग्री"* (39.5 °C).
   - The card is flagged: *"A word like 'saadhe/sawa/half' changes this number — enter the value yourself"*. **Yes, that's right** is disabled.
   - The ANM taps **✎ Change value** and enters 39.5 °C. That explicit value, labelled as entered by the ANM, is what is pre-filled. 39.5 °C is above the ATP threshold.
   - Before the fix, the system would have offered a clean-looking "39 °C".
6. *(Optional, unverified.)* Odia: choose **ଓଡ଼ିଆ**. On-device Odia has not been run on real Odia audio yet, and cloud Odia needs a Sarvam key plus separate consent. Say so if you show it.

> **Talking Point:** *"Every number the machine heard is read back and must be accepted or entered by the health worker; anything doubtful — 'not', 'yesterday', 'saadhe', numbers spoken in parts — cannot be accepted with one click, and anything unclear stays blank for human review. Voice only pre-fills; the rules engine and the ANM still decide. A value heard correctly but measured wrongly can still be wrong, which is why the reviewer signs off."*

5. Select body map locations: **Head** (headache, severity 3/5) + **Abdomen** (pain, severity 5/5 — patient describes it as severe)
6. ANM records vitals: RR 20, SpO2 97% on air, pulse 96, BP 118/76, temp 38.9 °C, Alert
7. ANM completes the **ATP red-flag screen** and ticks **"Severe pain anywhere in body"** (from the patient's report of severe abdominal pain)

**Eval Hits:** Multimodal (15%) — Odia voice + body map. Safety (20%) — read-back confirmation. Extraction (20%) — structured entities.

---

### Beat 3: Lab Report OCR + Verification (1:45–2:30)

**[Screen: Document Upload]**

> **Narrator:** *"The patient has a blood report from yesterday. Watch the OCR pipeline with self-verification."*

**Actions:**
1. Upload **lab report image** (printed CBC)
2. Show processing: OCR engine (Surya) → extraction
3. Results displayed:
   - **Platelets: 85,000/μL** ⚠️ below reference range (150K–400K) — shown for the clinician; **not a triage-rule input**
   - **Hb: 11.2 g/dL** ✅ Normal
   - Confidence: 91%
   - Gödel verification: ✅ Verified
4. Point out: **"Click any value to see the original image with bounding box"**
5. Show MAKER voting result: all 3 passes agree on 85,000

> **Talking Point:** *"Gödel self-verification: the system re-OCRs low-confidence regions, cross-checks drug names against RxNorm, and validates lab values against reference ranges. MAKER voting extracts critical values 3 times independently — it only accepts when all agree."*

**Eval Hits:** Multimodal (15%) — OCR. Extraction (20%) — source-linked, MAKER voting, Gödel verification.

---

### Beat 4: Triage — Rules Fire (2:30–3:30)

**[Screen: Triage Processing → Result]**

> **Narrator:** *"Now the rules engine — the heart of SEHAT AI. This part is deterministic: no LLM is involved in this decision."*

**Actions:**
1. Click **"Process Triage"** (calls `POST /api/v1/triage/process`)
2. Show the engine result exactly as returned:
   - 🔴 **Urgency: RED** — `determination: complete`
   - **Triggered rule:** `ATP_RED_SEVERE_PAIN` — *"Time-sensitive: severe pain anywhere in body"*
     - Evidence: `red_flag = severe_pain` (present on assessment — ANM red-flag screen)
     - Source: `ATP_2022` — AIIMS Triage Protocol, Singh/Sahu et al., *J Emerg Trauma Shock* 2022, Supplementary Table 1
   - **NEWS2: 2 (low)** — pulse 96 → 1, temp 38.9 → 1, others 0 (RCP 2017). Shown as a score; it does not drive this RED.
   - **qSOFA: 0 (negative)** — run because infection is suspected; a screen, not a diagnosis.
3. Point to the lab panel: *"Platelets 85K are below the reference range and linked to the lab image for the doctor — but no rule in this engine reads platelets, and they are not part of this decision."*
4. Show the **safety floor** (pre-seeded second case, same vitals, red-flag screen **not completed** and no flags recorded) → **🟡 YELLOW + "needs human review"** (`SAFETY_FLOOR_INSUFFICIENT_DATA`). GREEN is never assigned on incomplete data.

> **Talking Point (scripted, ~25 s):** *"RED comes from one cited rule — severe pain, AIIMS Triage Protocol — not from an AI guess. SEHAT doesn't diagnose dengue. When we audited our sources, 'platelets below 100K means RED' wasn't in WHO 2009 or India's 2023 dengue guideline, so we removed it. Honest limit: if the pain weren't recorded as severe and vitals stayed normal, today's engine would say GREEN — that's why a clinician reviews every case and a verified dengue pack is next. And the LLM layer, when added, is raise-only: tested so it can never lower this."*

> **If asked (detail):** ATP's generic red flags do not capture dengue-specific warning signs such as abdominal tenderness or persistent vomiting (NCVBDC 2023). That gap closes with the deferred dengue pack (docs/10 ADR-7).

**Eval Hits:** Safety (20%) — cited deterministic rule, evidence + threshold, safety floor, raise-only boundary. Review (15%) — explanation a clinician can check.

---

### Beat 5: Reviewer Dashboard + Sign-Off (3:30–4:15)

**[Screen: Switch to MO login → Dashboard]**

> **Narrator:** *"Now let's switch to the doctor's view."*

**Actions:**
1. Login as **Medical Officer** (Dr. Patel)
2. **Priority queue** appears — RED cases on top, ordered by rules engine
3. Open the RED case → **Triage Card** (shows `ATP_RED_SEVERE_PAIN` with its ATP_2022 source)
4. Show source-linked evidence:
   - Click "Fever 3 days" → audio transcript plays from 0:04
   - Click "Platelets 85K" → lab report image with bounding box (context for the clinician, not a rule input)
5. Show mandatory disclaimer: *"AI-drafted, pending review by qualified clinician"*
6. Point out what the engine did **not** decide: dengue-specific assessment (e.g. platelet trend, tourniquet test — NCVBDC 2023 triage parameters) is left to the clinician
7. **Sign off**: Dr. Patel approves under their own name and ID
8. Point out: *"Security-relevant actions are logged in an append-only, hash-chained audit trail — tamper-evident, not immutable"*

> **Talking Point:** *"Source-linked evidence — click any field, see the original source. The doctor knows exactly where each data point came from. Named sign-off — the doctor's name goes on every approval, not the AI's."*

**Eval Hits:** Review (15%) — priority queue, sign-off, source linking. Privacy (10%) — audit log.

---

### Beat 6: Referral + Closure (4:15–4:50)

**[Screen: Referral creation → Tracking]**

> **Narrator:** *"The doctor refers the patient to District Hospital Bhubaneswar. Watch what happens next."*

**Actions:**
1. Click **"Refer NOW"**
2. Referral packet auto-fills:
   - To: District Hospital, Bhubaneswar
   - Urgency: RED
   - Flags: `ATP_RED_SEVERE_PAIN` (ATP 2022)
   - Transport: 108 Ambulance
3. Show **referral tracking dashboard**:
   - Status: Referred → In Transit → (tracking continues)
   - "Overdue" alert if no update in 48h

> **Talking Point:** *"Most systems stop at referral creation. We track to CLOSURE — did the patient actually reach the specialist? If no update in 48 hours, the health worker gets an overdue alert. This is how you close the referral gap in rural India."*

**Eval Hits:** Review (15%) — referral. India (15%) — closure tracking.

---

### Beat 7: Close — Safety Architecture (4:50–5:00)

**[Screen: Summary slide]**

> **Narrator:** *"SEHAT AI: Rules-first triage. Source-linked evidence. Human sign-off. Referral closure. Built on Microsoft Semantic Kernel, aligned with Cognizant TriZetto AI Gateway."*

**Key message:**
- ✅ Non-diagnostic — AI never diagnoses
- ✅ Rules override LLM — deterministic safety
- ✅ Source-linked — every field traceable
- ✅ Human-in-the-loop — doctor signs off
- ✅ DPDP-ready — consent + PII redaction + audit trail
- ✅ Offline-capable — works at rural health camps

---

## Fallback Plan

| Failure | Fallback |
|:---|:---|
| **Microphone fails** | Use the **▶ Sample clip** button (synthetic audio, same backend path); mic needs localhost or HTTPS |
| **OCR server down** | Use pre-extracted JSON with canned result |
| **Voice STT fails** | The screen shows an explicit error (nothing inferred); type symptoms manually |
| **MedGemma unavailable** | Skip image demo, focus on voice + OCR |
| **Slow processing** | Pre-computed triage note cached, swap in after animation |
| **Reviewer dashboard blank** | Pre-seed queue with 3 demo cases |
| **Network issues** | Demo runs fully offline (PWA + local models) |
| **Total failure** | Slide deck walkthrough with screenshots of each screen |

---

## Demo Data Requirements

| Asset | Status | Description |
|:---|:---|:---|
| Odia audio recording | Needed to verify Odia (not yet available) | 15-second symptom description; synthetic or consented team recording only |
| Lab report image (CBC) | Needed | Printed with Platelets 85K, Hb 11.2 (reviewer context only) |
| Demo triage input | Ready | Reproducible request below; regression-tested in `backend/tests/rules/test_demo_vignette.py` |
| Pre-seeded queue cases | Needed | 3 cases: 1 RED, 1 YELLOW, 1 GREEN |
| Demo user accounts | Needed | ANM, Medical Officer, Supervisor |
| Body map SVG | Needed | Interactive body outline with regions |

---

> **Related Documents:**
> - [PRD](01_PRD.md) — User journeys this demo follows
> - [Frontend Specification](05_Frontend_Specification.md) — Screen designs for each beat
> - [Features Checklist](02_Features_Checklist.md) — Which features appear in demo
> - [Implementation Plan](08_Implementation_28h_Plan.md) — When demo assets are built

---

## Appendix: Reproducible Beat 4 request

```bash
# as mo_demo or anm_demo (JWT from POST /api/v1/auth/login)
curl -s -X POST http://localhost:8000/api/v1/triage/process \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scenario":"opd","age_years":28,"red_flag_screen_completed":true,
       "red_flags_present":["severe_pain"],"suspected_infection":true,
       "vitals":{"resp_rate":20,"spo2":97,"on_supplemental_oxygen":false,"pulse":96,
                 "sbp":118,"dbp":76,"temp_c":38.9,"consciousness":"A"}}'
# → urgency RED; triggered_rules: [ATP_RED_SEVERE_PAIN (ATP_2022)]; NEWS2 2 (low); qSOFA 0
# Safety-floor variant: "red_flag_screen_completed": false and no red_flags_present
#   → urgency YELLOW, needs_human_review true, SAFETY_FLOOR_INSUFFICIENT_DATA
```

Platelet count is **not** an engine input: a request containing `"platelets"` is rejected (`400 VALIDATION_ERROR`).
