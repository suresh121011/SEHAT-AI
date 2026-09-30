# SEHAT AI — Demo Script (5 Minutes)

> **Version:** 1.0 | **Date:** September 2026
> **Architecture Source:** [SEHAT AI v5.0 Final Architecture](sehat_ai_final_architecture__2.md) — §25
> **Setting:** Odisha PHC — Dengue triage scenario

---

## Demo Overview

| Item | Detail |
|:---|:---|
| **Duration** | 5 minutes |
| **Scenario** | 28-year-old patient at PHC Khurda with dengue warning signs |
| **Languages** | Odia voice input → English triage note |
| **Modalities** | Voice + lab report (OCR) + body map |
| **Story** | Intake → Triage → Review → Sign-off → Referral → Closure Tracking |

### Evaluation Beats Covered

| Criterion | Weight | Beat(s) |
|:---|:---:|:---|
| Safety-first triage | 20% | RED flag detection, rules override LLM, counterfactual |
| Extraction & summarisation | 20% | Source-linked note, OCR extraction, MAKER voting |
| Multimodal capability | 15% | Voice (Odia), OCR (lab report), body map |
| India-wide facility relevance | 15% | Odia language, PHC scenario, scenario rule pack |
| Human-review & escalation | 15% | Priority queue, sign-off, override, escalation timer |
| Privacy & responsible AI | 10% | Consent, PII redaction, non-diagnostic, audit |
| Demo quality | 5% | Smooth end-to-end story |

---

## Pre-Demo Checklist

- [ ] Backend running (`uvicorn main:app --reload`)
- [ ] Frontend running (`npm run dev`)
- [ ] Demo accounts seeded (patient, ANM, MO)
- [ ] Synthetic lab report image ready (dengue CBC with Platelets 85K)
- [ ] Odia TTS working (consent read-aloud)
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
6. Patient says **"haan"** (audio recorded as consent)

> **Talking Point:** *"Consent is layered — read aloud in the patient's language, with audio confirmation. DPDP-ready by design — built around DPDP Act Section 6 consent ahead of substantive duties starting May 2027."*

**Eval Hits:** Privacy (10%) — consent flow. India (15%) — Odia language.

---

### Beat 2: Voice Input + Read-Back (0:45–1:45)

**[Screen: Voice Recorder]**

> **Narrator:** *"The patient speaks in Odia. Here's the differentiator — Indian speech systems capture numbers only 2.7% of the time. We mitigate this."*

**Actions:**
1. Click **Record** → Patient speaks in Odia: *"ତିନି ଦିନ ହେଲା ଜ୍ୱର ହେଉଛି, ତାପମାନ 102 ଡିଗ୍ରୀ, ମୁଣ୍ଡ ବିନ୍ଧା ଓ ପେଟ ଯନ୍ତ୍ରଣା"*
   - (Translation: "Fever for 3 days, temperature 102 degrees, headache and abdominal pain")
2. System transcribes via **Presear Dakshini** (native Odia — no translation pipeline)
3. **TTS Read-Back**: *"I heard: fever 3 days, temperature 102°F, headache, abdominal pain. Is that correct?"*
4. Patient confirms ✅

> **Talking Point:** *"TTS read-back confirmation — every extracted number is read back. This is our answer to the 97% entity-miss rate in Indian ASR. No competitor does this."*

5. Select body map locations: **Head** (headache, severity 3/5) + **Abdomen** (pain, severity 4/5)

**Eval Hits:** Multimodal (15%) — Odia voice + body map. Safety (20%) — read-back confirmation. Extraction (20%) — structured entities.

---

### Beat 3: Lab Report OCR + Verification (1:45–2:30)

**[Screen: Document Upload]**

> **Narrator:** *"The patient has a blood report from yesterday. Watch the OCR pipeline with self-verification."*

**Actions:**
1. Upload **lab report image** (printed CBC)
2. Show processing: OCR engine (Surya) → extraction
3. Results displayed:
   - **Platelets: 85,000/μL** 🔴 CRITICAL (ref: 150K-400K)
   - **Hb: 11.2 g/dL** ✅ Normal
   - Confidence: 91%
   - Gödel verification: ✅ Verified
4. Point out: **"Click any value to see the original image with bounding box"**
5. Show MAKER voting result: all 3 passes agree on 85,000

> **Talking Point:** *"Gödel self-verification: the system re-OCRs low-confidence regions, cross-checks drug names against RxNorm, and validates lab values against reference ranges. MAKER voting extracts critical values 3 times independently — it only accepts when all agree."*

**Eval Hits:** Multimodal (15%) — OCR. Extraction (20%) — source-linked, MAKER voting, Gödel verification.

---

### Beat 4: Triage — Rules Fire (2:30–3:15)

**[Screen: Triage Processing → Result]**

> **Narrator:** *"Now watch the rules engine — the heart of SEHAT AI. This is deterministic, not AI-generated."*

**Actions:**
1. Click **"Process Triage"**
2. Show rules firing:
   - 🔴 **Rule: Dengue Warning Signs** (WHO 2009)
     - Platelets < 100K ✅ (85K from OCR)
     - Fever ≥ 3 days ✅ (from voice transcript)
     - Abdominal pain ✅ (from body map)
   - NEWS2 Score: 4 (Increased observation)
3. Show final urgency: **🔴 RED** (max of rules=RED, JEV=YELLOW, LLM=RED)
4. Show counterfactual:
   - *"If platelets > 100K → 🟡 YELLOW"*
   - *"If no fever → 🟡 YELLOW"*

> **Talking Point:** *"The rules engine decided RED — not the LLM. The LLM agreed, but even if it had said GREEN, the rules OVERRIDE. LLM can never lower urgency. This is our zero-hallucination guarantee for critical decisions."*

**Eval Hits:** Safety (20%) — rules override, counterfactual XAI. Extraction (20%) — structured triage.

---

### Beat 5: Reviewer Dashboard + Sign-Off (3:15–4:15)

**[Screen: Switch to MO login → Dashboard]**

> **Narrator:** *"Now let's switch to the doctor's view."*

**Actions:**
1. Login as **Medical Officer** (Dr. Patel)
2. **Priority queue** appears — RED cases on top, ordered by rules engine
3. Open the dengue case → **Triage Card**
4. Show source-linked evidence:
   - Click "Fever 3 days" → audio transcript plays from 0:04
   - Click "Platelets 85K" → lab report image with bounding box
5. Show mandatory disclaimer: *"AI-drafted, pending review by qualified clinician"*
6. Show **missing information**: "Tourniquet test not recorded"
7. **Sign off**: Dr. Patel approves under their own name and ID
8. Point out: *"Every action is logged in a tamper-evident, hash-chained audit trail"*

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
   - Flags: Dengue warning signs
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
| **Microphone fails** | Use pre-recorded Odia audio file |
| **OCR server down** | Use pre-extracted JSON with canned result |
| **Voice STT fails** | Type symptoms manually, explain STT would work |
| **MedGemma unavailable** | Skip image demo, focus on voice + OCR |
| **Slow processing** | Pre-computed triage note cached, swap in after animation |
| **Reviewer dashboard blank** | Pre-seed queue with 3 demo cases |
| **Network issues** | Demo runs fully offline (PWA + local models) |
| **Total failure** | Slide deck walkthrough with screenshots of each screen |

---

## Demo Data Requirements

| Asset | Status | Description |
|:---|:---|:---|
| Odia audio recording | Needed | 15-second symptom description |
| Lab report image (CBC) | Needed | Printed with Platelets 85K, Hb 11.2 |
| Pre-seeded queue cases | Needed | 3 cases: 1 RED, 1 YELLOW, 1 GREEN |
| Demo user accounts | Needed | ANM, Medical Officer, Supervisor |
| Body map SVG | Needed | Interactive body outline with regions |

---

> **Related Documents:**
> - [PRD](01_PRD.md) — User journeys this demo follows
> - [Frontend Specification](05_Frontend_Specification.md) — Screen designs for each beat
> - [Features Checklist](02_Features_Checklist.md) — Which features appear in demo
> - [Implementation Plan](08_Implementation_28h_Plan.md) — When demo assets are built
