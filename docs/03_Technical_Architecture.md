# SEHAT AI — Technical Architecture

> **Version:** 1.0 | **Date:** September 2026
> **Architecture Source:** [SEHAT AI v5.0 Final Architecture](sehat_ai_final_architecture__2.md)
> **Scope:** Condensed, implementation-oriented architecture. For full detail, see the source document.

---

## 1. Architecture Overview — 6-Layer Pipeline

### Key Principle

```
RULES set urgency (deterministic, cited, zero hallucination)
  ↕
JEV scores confidence (constrained, calibrated)
  ↕  
LLM extracts and summarises (guarded, source-linked)
  ↕
HUMAN signs off (mandatory, logged)
```

**The LLM is a TOOL, not the decision-maker.** It may only RAISE urgency. It may NEVER lower a flag set by the rules engine. Missing vitals resolve to "unknown — needs human review", never to "normal".

### 6-Layer Pipeline

```mermaid
flowchart TD
    subgraph L1["📥 LAYER 1: MULTIMODAL INPUT + CONSENT"]
        CONSENT["Layered Consent\n(Read aloud in Odia/Hindi,\naudio 'haan/yes')"]
        VOICE["🎤 Voice\n(Silero VAD → STT)"]
        DOC["📷 Document\n(Camera → OCR)"]
        XRAY["🩻 Medical Image\n(X-ray / ECG / CT)"]
        TEXT["⌨️ Text\n(Chat / Form)"]
        BODY["🫀 Body Map\n(SVG → Symptoms)"]
    end
    
    subgraph L2["🔒 LAYER 2: PRE-PROCESSING SAFETY"]
        PII["Presidio PII\n(Anonymise before LLM)"]
        INJECT["Prompt Injection\nDetector (LLM Guard)"]
        SCOPE["NeMo Guardrails\n(Topic boundaries)"]
    end
    
    subgraph L3["🧠 LAYER 3: UNDERSTANDING"]
        TRANS["IndicTrans2 → English"]
        NER_L["GLiNER + SNOMED-CT\n(Entity Extraction)"]
        MEDGEMMA["🆕 MedGemma\n(Image Findings Description)"]
        CRAG_L["CRAG Verified Context\n(Medical Guidelines)"]
        VOTE["MAKER Voting\n(Critical Value Agreement)"]
    end
    
    subgraph L4["🔴 LAYER 4: RULES + TRIAGE (Deterministic)"]
        RULES_L["AIIMS Protocol\n(Red / Yellow / Green)"]
        NEWS2["NEWS2 / qSOFA\n(Vital Scores)"]
        SCENARIO["Scenario Rule Pack\n(OPD / Maternal / Camp)"]
        JEV_L["JEV System One\n(Confidence Scoring)"]
    end
    
    subgraph L5["📝 LAYER 5: GENERATION (Guarded)"]
        EXTRACT["Structured Extraction\n(JSON schema)"]
        SUMMARY["Source-Linked Note\n(Every field → source)"]
        MISSING["Missing Info Detector\n+ Follow-Up Questions"]
        GUARD_OUT["Output Guard\n(Diagnosis/Rx filter)"]
    end
    
    subgraph L6["👨‍⚕️ LAYER 6: HUMAN REVIEW + CLOSURE"]
        QUEUE["Priority Queue\n(Rules-ordered)"]
        REVIEW["Sign-Off\n(Named reviewer)"]
        REFERRAL["Referral Packet\n+ Closure Tracking"]
        AUDIT["Immutable\nAudit Log"]
    end
    
    L1 --> L2 --> L3 --> L4
    L4 -->|"🔴 RED FLAG"| QUEUE
    L4 --> L5 --> L6
    
    style L1 fill:#4488ff,color:#fff
    style L2 fill:#ff4444,color:#fff
    style L3 fill:#8844ff,color:#fff
    style L4 fill:#ff8800,color:#fff
    style L5 fill:#0088ff,color:#fff
    style L6 fill:#00aa55,color:#fff
```

### Layer Characteristics

| Layer | Latency | Hallucination Risk | Purpose |
|:---|:---:|:---:|:---|
| **L1: Input + Consent** | Variable | N/A | Capture multimodal data with legal consent |
| **L2: Pre-Processing Safety** | < 50ms | **ZERO** | Strip PII before anything reaches the LLM |
| **L3: Understanding** | 1-3s | **Low** (NER + rules) | Translate, extract entities, verify with CRAG |
| **L4: Rules + Triage** | < 200ms | **ZERO** | Deterministic urgency via AIIMS + NEWS2 + qSOFA |
| **L5: Generation** | 1-3s | **Low** (guarded) | LLM generates source-linked note under guard |
| **L6: Human Review** | Variable | **ZERO** (human) | Final authority on all decisions |

---

## 2. Technology Stack

### Core Stack

| Component | Technology | Justification |
|:---|:---|:---|
| **Orchestration** | Microsoft Semantic Kernel (Python SDK) | Cognizant TriZetto alignment. Enterprise-first. |
| **Primary LLM (Cloud)** | Azure OpenAI GPT-4o | Cognizant alignment. HIPAA-eligible. Function calling. |
| **Local/Offline LLM** | Gemma 4 12B (DoRA fine-tuned, GGUF Q4) | Apache 2.0. 256K context. Edge: Q4 on CPU. |
| **Summarisation LLM** | Llama 4 Scout (17B active / 109B MoE) | 10M context window for full patient history. |
| **Rules Engine** | Custom Python — AIIMS + NEWS2 + qSOFA | 96.2% sensitive for 24h mortality. Zero hallucination. |
| **Triage Scoring** | JEV System One (TypeSafe AI) | 100ms. Calibrated probabilities. 444x cheaper than GPT-4. |
| **Frontend** | Next.js 15 (PWA, offline-first) | SSR for slow networks. Service Worker caching. |
| **Backend** | FastAPI (Python) | Async. Matches SK Python SDK. |
| **Database (Hackathon)** | SQLite + aiosqlite | Zero-setup. Auto-creates on first run. |
| **Inference (Cloud)** | SGLang | RadixAttention prefix caching = 3-5x faster. |
| **Inference (Edge)** | Ollama (GGUF) | Local inference for offline mode. |

### Voice Stack

| Component | Technology | Size | Purpose |
|:---|:---|:---:|:---|
| **VAD** | Silero VAD | ~2MB | Language-agnostic voice activity detection, < 1ms |
| **STT (Online)** | Silero STT + Saaras V4 | ~50MB | Code-mix optimised, 5 output formats |
| **STT (Offline)** | Silero STT + IndicConformer 30M | ~100MB | 26-language offline ASR |
| **Odia Native** | Presear Dakshini | — | Native Odia — no translation pipeline needed |
| **Translation** | IndicTrans2 (1B) | ~700MB | 22 scheduled Indian languages. ONNX INT8. |
| **TTS (Read-back)** | Indic Parler-TTS | — | Read-back confirmation in 23 languages |

### OCR + Image Stack

| Component | Technology | Size | Purpose |
|:---|:---|:---:|:---|
| **Handwritten Rx** | Chandra OCR 2 (4B, INT8 QAT) | ~4GB | 85.9% olmOCR benchmark. 90+ languages. |
| **Printed Reports** | Surya OCR + PaddleOCR | ~500MB | CPU-friendly. Table extraction. |
| **Medical Images** | MedGemma (4B, INT8) | ~5GB | X-ray, ECG, wound photo descriptions (NOT diagnosis) |
| **Image Fallback** | GPT-4o Vision (Azure) | Cloud | Cognizant-aligned cloud fallback |
| **OCR Verification** | Gödel CoVe + RxNorm | — | Self-verification + drug name validation |

### Safety Stack

| Component | Technology | Purpose |
|:---|:---|:---|
| **PII Redaction** | Microsoft Presidio + India patterns | ABHA, Aadhaar, phone, PAN. Redact BEFORE cloud. |
| **Dialogue Guard** | NeMo Guardrails (Colang 2.0) | Topic boundaries. "Cannot diagnose" enforced. |
| **I/O Filter** | LLM Guard | Prompt injection. Hallucination detection. |
| **Language Filter** | Custom regex + ConstitutionalAI | Block "diagnosed with", "prescribe", "take [drug]". |
| **NER** | GLiNER + SNOMED-CT + RxNorm + ICD-10 | Standardised medical entity extraction. |

### GPU Requirements

| Model | Params | Quantisation | VRAM | Latency |
|:---|:---:|:---|:---:|:---:|
| Gemma 4 12B | 12B | AWQ 4-bit | ~8GB | ~200ms |
| Llama 4 Scout | 17B active | AWQ 4-bit | ~12GB | ~300ms |
| Chandra OCR 2 | 4B | INT8 QAT | ~4GB | ~200ms/page |
| MedGemma | 4B | INT8 | ~5GB | ~500ms/image |
| GLiNER | 300M | FP32 | ~1GB | ~50ms |
| **Total Local GPU** | — | — | **~36GB** | — |

> Everything runs on a single A100 (80GB) with room to spare. For hackathon demo, an A10G (24GB) handles most models. For edge/offline, the Lite stack fits in ~3.1GB on any 8GB tablet.

---

## 3. Data Flow Pipeline

```mermaid
sequenceDiagram
    participant P as 📱 Patient / Health Worker
    participant VAD as 🎤 Silero VAD + STT
    participant PII as 🔒 Presidio
    participant SK as 🧠 Semantic Kernel
    participant NER as 🏷️ GLiNER + SNOMED
    participant RULES as 🔴 Rules Engine
    participant JEV as 🟠 JEV System One
    participant LLM as 🔵 GPT-4o / Gemma 4
    participant GUARD as 🛡️ Output Guard
    participant DOC as 👨‍⚕️ Medical Officer
    
    P->>VAD: Speaks symptoms (Odia/Hindi/English)
    VAD->>VAD: Silero VAD detects speech (sub-ms)
    Note over VAD: STT transcription + IndicTrans2 → English
    
    P->>SK: Uploads lab report / X-ray
    Note over SK: OCR (Chandra/Surya) or MedGemma
    
    SK->>PII: Raw text (transcript + OCR)
    PII->>PII: Strip names, ABHA, Aadhaar, phone
    PII->>SK: Anonymised text
    
    SK->>NER: Extract entities
    NER->>NER: Symptoms, drugs, values → SNOMED codes
    
    NER->>RULES: Structured entities + vitals
    RULES->>RULES: AIIMS Red/Yellow/Green + NEWS2 + qSOFA
    
    alt 🔴 RED FLAG
        RULES->>DOC: IMMEDIATE ESCALATION (rule + source cited)
    else ✅ No Red Flag
        RULES->>JEV: Entities + vitals → confidence score
    end
    
    JEV->>LLM: Entities + CRAG-verified context
    LLM->>LLM: MAKER voting on critical values (3 passes)
    LLM->>LLM: Generate source-linked note + detect missing fields
    LLM->>GUARD: Draft output
    GUARD->>GUARD: Filter diagnosis/Rx + PII re-check + HASSUM entropy
    
    GUARD->>DOC: Triage card in review queue
    DOC->>DOC: Review, edit, sign under own ID
    DOC->>DOC: Approve referral packet (tracked to closure)
```

---

## 4. Voice Pipeline

```mermaid
flowchart LR
    MIC["🎤 Microphone"] --> VAD["Silero VAD\n(2MB ONNX)\n< 1ms latency"]
    VAD -->|"Speech only"| LANG{"Language?"}
    
    LANG -->|"Odia"| DAKSH["Presear Dakshini\n(Native Odia)"]
    LANG -->|"Hindi/English"| STT_ROUTE{"Online?"}
    
    STT_ROUTE -->|"✅ Online"| SAARAS["Saaras V4\n(Code-mix, 5 outputs)"]
    STT_ROUTE -->|"❌ Offline"| INDIC["Silero STT +\nIndicConformer 30M"]
    
    DAKSH --> READBACK["📢 TTS Read-Back\n'I heard 102°F.\nCorrect?'"]
    SAARAS --> READBACK
    INDIC --> READBACK
    
    READBACK --> TRANS["IndicTrans2 → English"]
    TRANS --> NER_V["GLiNER NER\n+ SNOMED codes"]
```

### Voice Strategy by Scenario

| Scenario | Primary STT | Fallback | Rationale |
|:---|:---|:---|:---|
| **Odia speaker at PHC** | Presear Dakshini | Silero STT + IndicTrans2 | Native Odia — no translation needed |
| **Hindi speaker online** | Saaras V4 API | Silero STT | Best for code-mixing |
| **Hindi speaker offline** | Silero STT | IndicConformer 30M | Both run locally |
| **Health camp (no internet)** | Silero STT + IndicConformer | Manual text entry | Entire pipeline on-device |

### Critical Mitigation: TTS Read-Back

Indian ASR captures entity-dense tokens (numbers, drug names) only **0.027–0.16 of the time** (arXiv:2605.03073). After every extraction, TTS reads back every extracted number for patient confirmation. **No competitor does this.**

---

## 5. OCR Pipeline

```mermaid
flowchart TD
    IMG["📷 Document Image"] --> QUALITY["Quality Check\n(blur, skew, lighting)"]
    QUALITY -->|"Poor"| RETAKE["Ask to retake"]
    QUALITY -->|"OK"| CLASSIFY{"Document Type?"}
    
    CLASSIFY -->|"Handwritten Rx"| CHANDRA["Chandra OCR 2\n(4B, INT8 QAT)"]
    CLASSIFY -->|"Printed report"| SURYA["Surya / PaddleOCR\n(Table-aware, CPU)"]
    CLASSIFY -->|"Mixed"| BOTH["Both → merge"]
    
    CHANDRA --> GODEL
    SURYA --> GODEL
    BOTH --> GODEL
    
    subgraph GODEL["🔍 Gödel Self-Verification"]
        WORD["Word-level confidence"]
        LOW["Low-conf: zoom + re-OCR"]
        RX["Drug → RxNorm fuzzy match"]
        REF["Lab values → reference range"]
    end
    
    GODEL --> CONF{"Confidence?"}
    CONF -->|"> 0.85"| ACCEPT["✅ Accept"]
    CONF -->|"0.5-0.85"| AMBER["🟡 Show both to reviewer"]
    CONF -->|"< 0.5"| HUMAN["🔴 Flag for human entry"]
```

### MedGemma — Medical Image Understanding

| ❌ DIAGNOSIS (Illegal) | ✅ DESCRIPTION (What MedGemma Does) |
|:---|:---|
| "This X-ray shows pneumonia" | "Opacification in right lower lobe. **Findings pending radiologist review.**" |
| "Patient has MI" | "ECG shows ST-segment elevation in V1-V4. **Specialist review required.**" |

MedGemma is the **primary** image model (runs locally, DPDP-ready — patient images never leave the device). GPT-4o Vision via Azure is the cloud fallback.

---

## 6. Rules Engine — AIIMS Protocol

### Core: AIIMS Triage (96.2% sensitive for 24h mortality)

The AIIMS Triage Protocol (ATP) Red criteria were **96.2% sensitive** for 24-hour mortality in 13,754 patients (Singh, Sahu et al., *JETS* 2022;15(3):124-7, doi:10.4103/jets.jets_146_21, PubMed 36353399). Adopted at AIIMS Bhubaneswar (Odisha).

> **Corrected 2026-09-30 (Phase 2).** Thresholds below are taken verbatim from the published ATP **Supplementary Table 1** and RCP NEWS2 Charts 1–2. Earlier versions of this section listed unsourced values (RR >30/<8, pulse >130, GCS <13, "temp <35 → RED"). Implementation, full rule list, source registry and decision record: [`10_Safety_Rules_Engine.md`](10_Safety_Rules_Engine.md).

```python
# RULES ENGINE — DETERMINISTIC, ZERO HALLUCINATION (backend/app/rules/)
# Each rule has: id, urgency, reason, source_id, predicate, evidence {value, threshold}

ATP_RED_CRITERIA = {   # ATP 2022, Supplementary Table 1 — any one present → RED
    "airway":      "stridor/noisy breathing | facial angioedema | active seizures",
    "breathing":   "incomplete sentences | audible wheeze | RR > 22 or < 10 | SpO2 < 90%",
    "circulation": "pulse < 50 or > 120 (without fever) | SBP > 220 or DBP > 110 | "
                   "SBP < 90 or DBP < 60 | shock index > 1 | active bleeding",
    "disability":  "altered sensorium (responds only to Voice/Pain, or Unresponsive)",
    "time_sensitive": "acute chest pain < 24h | limb weakness < 24h | suspected stroke < 24h | "
                   "dangerous-mechanism trauma | acute SOB < 12h | limb ischaemia < 48h | allergic reaction | "
                   "scrotal pain (young male) | severe pain | sudden abdominal pain | sudden headache | "
                   "urinary retention | fever with temp > 39°C or immunocompromise | syncope | needle prick",
    "increased_urgency": "abdominal pain + vaginal bleeding | agitated/violent | poisoning/snake/scorpion | "
                   "3rd trimester with abdominal pain/vaginal bleeding",
}

# NEWS2 (RCP 2017 Chart 1) — scored separately from interpretation
#   RR:    ≤8→3, 9–11→1, 12–20→0, 21–24→2, ≥25→3
#   SpO2 Scale 1: ≤91→3, 92–93→2, 94–95→1, ≥96→0   (Scale 2 for hypercapnic respiratory failure)
#   Air or oxygen: oxygen→2
#   SBP:   ≤90→3, 91–100→2, 101–110→1, 111–219→0, ≥220→3
#   Pulse: ≤40→3, 41–50→1, 51–90→0, 91–110→1, 111–130→2, ≥131→3
#   ACVPU: Alert→0, C/V/P/U→3
#   Temp:  ≤35.0→3, 35.1–36.0→1, 36.1–38.0→0, 38.1–39.0→1, ≥39.1→2
# RCP Chart 2 → SEHAT urgency: ≥7 → RED; 5–6 → YELLOW; any single parameter = 3 → YELLOW; 0–4 → no escalation
# Not used under 16 years or in pregnancy (RCP). Missing parameters are never assumed normal.

# qSOFA (Sepsis-3, JAMA 2016) — only with suspected infection: RR ≥ 22, altered mentation, SBP ≤ 100
# ≥ 2 → YELLOW minimum. A positive screen, not a diagnosis.

# GREEN must be earned: missing vitals / red-flag screen not completed / age < 14
#   → YELLOW + needs_human_review (never "normal")

# THE CARDINAL RULE: LLM can NEVER lower urgency
def enforce_raise_only(deterministic, suggested):
    """final = max(deterministic, suggested); downgrades refused and recorded."""
    urgency_order = {"RED": 3, "YELLOW": 2, "GREEN": 1}
    return max(deterministic, suggested or deterministic, key=urgency_order.__getitem__)
```

### 7 Scenario Rule Packs

| Scenario | Urgency rules (raise-only) | Advisories (never change urgency) | Source |
|:---------|:----------|:-------|:---------------|
| **OPD Triage** | ATP + NEWS2 (+ qSOFA if infection suspected) | — | ATP 2022, RCP NEWS2 |
| **Maternal** | Any danger sign → YELLOW; Hb < 7 g/dL → YELLOW; ATP RED for seizures, 3rd-trimester bleed/pain, BP > 220/110. No NEWS2 in pregnancy | Age < 18 / > 35 (pending verification) | MoHFW MCP card, WHO Hb 2024 |
| **Chronic NCD** | BP > 180/110 on any reading → YELLOW (refer; > 220/110 RED via ATP). Needs 2 readings for GREEN | — | IHCI protocol |
| **Health Camp** | ATP + NEWS2 | CBAC > 4 → prioritise NCD screening | NPCDCS CBAC |
| **Campus Fever** | ATP (temp > 39°C RED) + qSOFA | Fever ≥ 38°C + cough, onset ≤ 10 d = WHO ILI | WHO ILI 2014 |
| **Occupational** | ATP + NEWS2 | Hearing shift ≥ 10 dB avg at 2/3/4 kHz → audiology review | 29 CFR 1910.95 (US) |
| **Referral** | ATP + NEWS2 | Escort, transport, identity, consent missing → packet incomplete | SEHAT policy |

---

## 7. Extraction & Source-Linked Notes

Every field links to its source. When a reviewer clicks any field, they see the original transcript segment or OCR bounding box.

```python
class SourceLinkedField:
    field_name: str          # e.g., "chief_complaint"
    value: str               # e.g., "fever for 3 days, 102°F"
    source_type: str         # "transcript" | "ocr" | "body_map" | "manual"
    source_ref: SourceRef    # transcript timestamp or OCR bbox
    confidence: float        # 0.0 - 1.0
    snomed_code: Optional[str]
    status: str              # "confirmed" | "unconfirmed" | "disputed"
```

### Missing Information Detection

Per-scenario required fields trigger follow-up questions in the patient's language:
- **OPD:** chief_complaint, duration, severity, SpO2, BP, pulse
- **Maternal:** LMP, EDD, gravida/parity, Hb, BP, danger signs
- **Chronic NCD:** 2 BP readings, blood sugar, current drugs, adherence

---

## 8. Anti-Hallucination — 5-Layer Defence

```mermaid
flowchart TD
    subgraph AH1["Layer 1: CRAG — Corrective RAG"]
        R1["Retrieve from guidelines"] --> R2["Evaluate quality"]
        R2 -->|"High"| USE["Use context"]
        R2 -->|"Low"| DISCARD["Discard → rules only"]
    end
    
    subgraph AH2["Layer 2: MAKER Voting"]
        E1["Extract (temp=0.1)"] --> AGREE{"3 agree?"}
        E2["Extract (temp=0.2)"] --> AGREE
        E3["Extract (temp=0.3)"] --> AGREE
        AGREE -->|"Yes"| ACC["✅ Accept (0.95)"]
        AGREE -->|"No"| FLAG["🟡 Human entry"]
    end
    
    subgraph AH3["Layer 3: HASSUM Uncertainty"]
        SEM["Semantic entropy"] -->|"Low"| PROC["Proceed"]
        SEM -->|"High"| ESC["Force human review"]
    end
    
    subgraph AH4["Layer 4: Output Guards"]
        DIAG["Block diagnosis"] --- RX_F["Block prescription"] --- PII2["PII re-check"]
    end
    
    subgraph AH5["Layer 5: Human Sign-Off"]
        HSO["Named reviewer verifies every field"]
    end
    
    AH1 --> AH2 --> AH3 --> AH4 --> AH5
```

- **MAKER Voting** (Cognizant AI Lab, arXiv:2511.09030): Extract critical values 3x independently, accept only on agreement.
- **HASSUM** (Cognizant AI Lab, arXiv:2608.14707): High semantic entropy → force human review.
- **CRAG**: Discard low-quality retrieval before the LLM sees it.

---

## 9. Semantic Kernel & TriZetto Integration

### SK Plugin Architecture

```python
kernel = Kernel()
kernel.add_service(AzureChatCompletion(...))

# Input Plugins
kernel.add_plugin(VoicePlugin(),       "VoiceInput")
kernel.add_plugin(OCRPlugin(),         "MedicalOCR")
kernel.add_plugin(ImagePlugin(),       "MedicalImaging")  # MedGemma
kernel.add_plugin(BodyMapPlugin(),     "BodyMap")

# Processing Plugins
kernel.add_plugin(TranslationPlugin(), "Translator")      # IndicTrans2
kernel.add_plugin(NERPlugin(),         "MedicalNER")       # GLiNER + SNOMED
kernel.add_plugin(CRAGPlugin(),        "VerifiedRetrieval")

# Decision Plugins (RULES FIRST)
kernel.add_plugin(RulesEngine(),       "RedFlagRules")
kernel.add_plugin(JEVTriagePlugin(),   "TriageScoring")

# Output Plugins
kernel.add_plugin(NoteGenerator(),     "TriageNote")
kernel.add_plugin(FHIRPlugin(),        "FHIRExport")
kernel.add_plugin(ReferralPlugin(),    "ReferralPacket")

# Safety Plugins
kernel.add_plugin(PresidioPlugin(),    "PIIAnonymizer")
kernel.add_plugin(NeMoPlugin(),        "DialogueGuard")
kernel.add_plugin(AuditPlugin(),       "AuditLogger")
```

### SEHAT AI → TriZetto Pipeline

```mermaid
flowchart LR
    subgraph SEHAT["🏥 SEHAT AI\nPatient-Facing Triage"]
        S1["Patient Voice/Text/Image"]
        S2["AI Triage Assessment\n(AIIMS Red/Yellow/Green)"]
        S3["Structured Triage Note\n(FHIR R4 Bundle)"]
    end
    
    subgraph Gateway["🔷 TriZetto AI Gateway\n(Cognizant's Product)"]
        G1["Semantic Kernel Orchestrator"]
        G2["Claims Processing Agent"]
        G3["Prior Auth Agent"]
        G4["Care Coordination Agent"]
    end
    
    S1 --> S2 --> S3
    S3 -->|"FHIR R4 API"| G1
    G1 --> G2 & G3 & G4
    
    style SEHAT fill:#00aa55,color:#fff
    style Gateway fill:#0078D4,color:#fff
```

> **The Pitch:** *"SEHAT AI is the patient-facing triage front-end. Its FHIR-compliant output flows seamlessly through TriZetto AI Gateway into Cognizant's administrative backend. Built on Microsoft Semantic Kernel — the same orchestration framework powering TriZetto."*

---

## 10. Offline / Edge Architecture

### Edge Stack (~3.1GB total)

| Component | Edge Size | Function |
|:---|:---:|:---|
| Silero VAD | 2MB | Voice activity detection |
| Silero STT | ~50MB | Basic speech-to-text |
| IndicConformer 30M | ~100MB | 26-language ASR |
| IndicTrans2 INT8 | ~700MB | Translation |
| Surya OCR | ~500MB | Printed document OCR |
| GLiNER INT8 | ~300MB | Medical entity extraction |
| Rules Engine | ~100KB | AIIMS + NEWS2 + scenarios |
| Gemma 4 Q4 | ~1.5GB | Offline summarisation |
| **TOTAL** | **~3.1GB** | Full triage pipeline |

**"Lite" mode** runs rules only without any LLM — this doubles as ICMR's required fallback when AI fails.

---

## 11. Agentic GraphRAG

Instead of naive vector RAG, an AI agent traverses a SNOMED-CT medical knowledge graph:

```
Patient: "Chest pain and difficulty breathing, I have BP problem"
1. GLiNER extracts: [chest_pain, breathing_difficulty, hypertension]
2. SNOMED-CT maps: [29857009, 267036007, 38341003]
3. GraphRAG traverses → cardiac emergency path
4. Retrieves guideline: "Chest pain with risk factors = RED minimum"
5. Rules engine confirms: RED
6. Output includes citation: "AIIMS Protocol, Decision Point B"
```

---

## 12. Explainable AI — Counterfactual Triage

Instead of SHAP values (which doctors don't understand), natural language counterfactuals:

```
🔴 RED — Dengue warning signs
📋 WHY: Platelets 85K (< 100K) + Fever 3 days + Abdominal pain
🔄 WHAT WOULD CHANGE IT:
  • If platelets > 100K → YELLOW
  • If no fever → YELLOW
  • If SpO2 > 96% → still RED (platelets)
📖 RULE: WHO Dengue Classification 2009 + AIIMS Protocol
```

**Research:** IEEE 2025 — Counterfactual XAI improves clinician trust by 47%.

---

## 13. Research Foundation

| Source | Paper/Technique | Application |
|:---|:---|:---|
| Google DeepMind | AI Co-Clinician (2026) | "Triadic care" philosophy (AI + Patient + Physician) |
| Google | MedGemma (2025) | Medical image understanding |
| Cognizant AI Lab | MAKER (arXiv:2511.09030) | Multi-pass voting on critical values |
| Cognizant AI Lab | HASSUM (arXiv:2608.14707) | Semantic uncertainty escalation |
| IEEE/NeurIPS | Counterfactual XAI | Natural language explanations |
| AIIMS | Triage Protocol (PubMed 36353399) | 96.2% sensitive Red criteria |

---

> **Related Documents:**
> - [PRD](01_PRD.md) — Product requirements and rationale
> - [Security & Privacy](04_Security_Privacy_Access.md) — Safety pipeline details
> - [API Contracts](06_API_Data_Contracts.md) — JSON schemas and endpoints
> - [Implementation Plan](08_Implementation_28h_Plan.md) — Phase-by-phase build
