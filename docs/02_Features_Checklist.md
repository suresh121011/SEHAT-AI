# SEHAT AI — Features Checklist

> **Version:** 1.0 | **Date:** September 2026
> **Architecture Source:** [SEHAT AI v5.0 Final Architecture](sehat_ai_final_architecture__2.md)

---

## Feature Priority Tiers

### 🔴 Tier 1: Must-Have (MVP) — Phases P1–P8

These features are required for a viable demo and cover **85%+** of evaluation marks.

| # | Feature | Eval Criterion (Weight) | Phase | Differentiator? |
|:---:|:---|:---|:---:|:---:|
| 1 | **AIIMS Red/Yellow/Green triage protocol** (deterministic rules, 96.2% sensitive for 24h mortality) | Safety (20%) | P2 | ✅ #5 |
| 2 | **NEWS2 + qSOFA vital scoring** (deterministic, cited) | Safety (20%) | P2 | |
| 3 | **7 scenario-specific rule packs** (OPD, Maternal, Chronic NCD, Health Camp, Campus Fever, Occupational, Referral) | Safety (20%), India (15%) | P2 | ✅ #5 |
| 4 | **Rules override LLM** — LLM can NEVER lower urgency | Safety (20%) | P2 | ✅ #9 |
| 5 | **Layered consent** in patient's language (TTS read-aloud, audio "haan/yes") | Privacy (10%) | P3 | |
| 6 | **PII redaction** (Presidio + ABHA, Aadhaar, phone, PAN) BEFORE cloud LLM | Privacy (10%) | P3 | |
| 7 | **Tamper-evident audit log** (hash-chained SHA-256) | Privacy (10%) | P3 | |
| 8 | **Voice input** (Silero VAD + STT) in Odia/Hindi/English | Multimodal (15%) | P4 | |
| 9 | **TTS read-back confirmation** ("I heard temp 102°F — correct?") | Multimodal (15%), Safety (20%) | P4 | ✅ #6 |
| 10 | **Presear Dakshini** native Odia voice agent | Multimodal (15%), India (15%) | P4 | ✅ #10 |
| 11 | **OCR — printed lab reports** (Surya/PaddleOCR, table-aware) | Multimodal (15%), Extraction (20%) | P5 | |
| 12 | **OCR — handwritten prescriptions** (Chandra OCR 2, INT8 QAT) | Multimodal (15%), Extraction (20%) | P5 | |
| 13 | **Gödel OCR self-verification** (CoVe + RxNorm cross-check) | Extraction (20%) | P5 | ✅ #7 |
| 14 | **Structured JSON extraction** (not free text) | Extraction (20%) | P6 | |
| 15 | **Source-linked notes** — every field traces to transcript/OCR/manual source | Extraction (20%) | P6 | ✅ #1 |
| 16 | **MAKER voting** on critical values (multi-pass extraction agreement) | Extraction (20%) | P6 | ✅ #2 |
| 17 | **Missing information detection** + follow-up questions in patient's language | Extraction (20%) | P6 | |
| 18 | **Patient intake UI** — voice recorder, body map, document upload, scenario selector | Multimodal (15%), India (15%) | P7 | |
| 19 | **Interactive body map** (SVG → symptom selection) | Multimodal (15%) | P7 | |
| 20 | **Reviewer dashboard** — priority queue, triage cards, source-linked evidence | Review (15%) | P8 | |
| 21 | **Named sign-off** (reviewer ID + name on every approval) | Review (15%) | P8 | |
| 22 | **Override with reason code** (structured justification for lowering urgency) | Review (15%), Safety (20%) | P8 | |
| 23 | **Escalation timer** (3-min auto-escalation for unacknowledged RED) | Review (15%), Safety (20%) | P8 | |
| 24 | **Counterfactual XAI** ("If red-flag screen not completed → YELLOW + human review") | Review (15%), Safety (20%) | P8 | ✅ #11 |
| 25 | **Non-diagnostic language filter** (regex + architectural enforcement) | Safety (20%), Privacy (10%) | P3/P6 | ✅ #14 |

### 🟡 Tier 2: Should-Have (High Differentiation) — Phases P9–P10

| # | Feature | Eval Criterion (Weight) | Phase | Differentiator? |
|:---:|:---|:---|:---:|:---:|
| 26 | **Referral closure tracking** (status: referred → in transit → reached → seen → outcome) | Review (15%), India (15%) | P9 | ✅ #4 |
| 27 | **Referral packet** with flags, evidence, transport plan | Review (15%) | P9 | |
| 28 | **Overdue referral alerts** (48h no-update trigger) | Review (15%) | P9 | |
| 29 | **FHIR R4 bundle export** | India (15%) | P9 | ✅ #13 |
| 30 | **Governance telemetry tile** (override rate, cost/note, avg review time) | Safety (20%) | P10 | ✅ #12 |
| 31 | **Test evidence slide** (red-flag sensitivity on 50 vignettes, prompt-injection test) | Demo (5%), Privacy (10%) | P10 | |
| 32 | **Model card with limitations** | Privacy (10%) | P10 | |
| 33 | **Synthetic demo data** (50 triage vignettes, lab reports, prescriptions) | Demo (5%) | P10 | |
| 34 | **DPDP-ready privacy design** (consent, retention, erasure, emergency bypass) | Privacy (10%) | P3/P10 | ✅ #15 |
| 35 | **HASSUM uncertainty escalation** (high entropy → force human review) | Safety (20%) | P6/P10 | ✅ #3 |

### 🟢 Tier 3: Nice-to-Have / Stretch — Phase P11 + Future

| # | Feature | Eval Criterion (Weight) | Phase | Differentiator? |
|:---:|:---|:---|:---:|:---:|
| 36 | **PWA offline-first** (Service Worker + SQLite + quantised edge models) | India (15%) | P11 | |
| 37 | **Edge deployment** (~3.1GB total on 8GB tablet) | India (15%) | P11 | |
| 38 | **Encrypted sync-on-reconnect** | India (15%), Privacy (10%) | P11 | |
| 39 | **MedGemma medical image understanding** (X-ray, ECG, wound photo descriptions) | Multimodal (15%) | P5 (if time) | |
| 40 | **CRAG anti-hallucination** (discard bad retrieval before LLM) | Safety (20%) | Future | ✅ #8 |
| 41 | **Agentic GraphRAG** (SNOMED-CT knowledge graph traversal) | Extraction (20%) | Future | |
| 42 | **Federated learning architecture** (FedAvg + differential privacy) | Privacy (10%) | Future | |
| 43 | **SNOMED-CT entity mapping** via GLiNER NER | Extraction (20%) | Future | |

---

## Explicit Non-Goals

| Non-Goal | Reason |
|:---|:---|
| ❌ Diagnosis or prescription | Illegal (TPG 5.4, ICMR 2023). Architectural enforcement via language filter. |
| ❌ "AI Doctor" branding | Cognizant moved away from this in 2026. We are "triage support". |
| ❌ Free-chat symptom collection | Oxford 2026 RCT showed no benefit. Protocol-driven structured intake instead. |
| ❌ LLM-ordered priority queue | Introduces AI bias. Rules engine orders the queue deterministically. |
| ❌ Diagnostic accuracy claims | No valid basis. Report metrics on synthetic test sets only. |
| ❌ DPDP/CDSCO compliance claims | Not yet validated. "DPDP-ready by design" and "research prototype" only. |
| ❌ Image interpretation as findings | Invites CDSCO device regulation. Attach + display + flag out-of-range only. |
| ❌ Storing raw audio after sign-off | DPDP data minimisation. Retention countdown enforced. |
| ❌ Comparison against doctors | No valid benchmark exists for this prototype. |

---

## 28-Hour Implementation Priority Order

| Priority | Phase | Duration | Features Covered | Cumulative Coverage |
|:---:|:---|:---:|:---|:---|
| 1 | **P1: Foundation** | 3h | FastAPI + SQLite + Next.js + SK + Auth | Infrastructure |
| 2 | **P2: Rules Engine** | 3h | Features #1-4 | Safety 20%, Review 15% |
| 3 | **P3: Consent + PII** | 2h | Features #5-7, #25 | + Privacy 10% |
| 4 | **P4: Voice Pipeline** | 3h | Features #8-10 | + Multimodal 15% |
| 5 | **P5: OCR Pipeline** | 3h | Features #11-13 | + Multimodal, Extraction |
| 6 | **P6: Extraction** | 3h | Features #14-17 | + Extraction 20% |
| 7 | **P7: Patient UI** | 3h | Features #18-19 | + India 15% |
| 8 | **P8: Reviewer Dashboard** | 3h | Features #20-24 | + Review 15% |
| 9 | **P9: Referral + Closure** | 2h | Features #26-29 | Differentiation |
| 10 | **P10: Data + Demo** | 2h | Features #30-35 | Demo readiness |
| 11 | **P11: Edge/Offline** | 2h | Features #36-38 | Full India coverage |

> **Stop-point:** After P8 (23 hours), we have a complete MVP demo covering ~85% of evaluation marks. P9-P11 are stretch goals.

---

## Evaluation Weight Distribution

| Criterion | Weight | Features Serving This Criterion | # of Features |
|:---|:---:|:---|:---:|
| **Safety-first triage** | 20% | #1, #2, #3, #4, #9, #22, #23, #24, #25, #30, #35 | 11 |
| **Extraction & summarisation** | 20% | #11, #12, #13, #14, #15, #16, #17 | 7 |
| **Multimodal capability** | 15% | #8, #9, #10, #11, #12, #18, #19, #39 | 8 |
| **India-wide facility relevance** | 15% | #3, #10, #18, #26, #29, #36, #37, #38 | 8 |
| **Human-review & escalation** | 15% | #20, #21, #22, #23, #24, #26, #27, #28 | 8 |
| **Privacy & responsible AI** | 10% | #5, #6, #7, #25, #31, #32, #34 | 7 |
| **Demo quality** | 5% | #31, #33 | 2 |

> **No evaluation criterion is under-served.** Safety and Extraction (the two 20% criteria) have the most features assigned.

---

> **Related Documents:**
> - [PRD](01_PRD.md) — Full product requirements and rationale
> - [Technical Architecture](03_Technical_Architecture.md) — How features are implemented
> - [Implementation Plan](08_Implementation_28h_Plan.md) — Phase-by-phase execution
