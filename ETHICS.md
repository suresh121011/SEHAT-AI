# SEHAT AI: ethics, law and patient rights

> **Not legal advice.** This is product context written by the project team, current to 12 November 2026. Items
> marked *verify* must be checked against the primary text before anyone relies on them. Before any real patient,
> take advice from a qualified lawyer and an Institutional Ethics Committee.

## 1. Position

SEHAT AI is a **research prototype, not a clinically validated device**. It helps health workers at Indian primary
health centres collect a structured, source-linked intake and see a protocol-based urgency level. It does not
diagnose, prescribe, counsel or decide. A registered medical practitioner reviews every case and signs off by name.

| SEHAT does | SEHAT does not |
|:---|:---|
| Apply published triage protocols deterministically (AIIMS Triage Protocol, NEWS2, qSOFA) | Diagnose, list differentials, suggest medicines or doses |
| Extract what the patient and documents say, with a quote or source for every value | Let a model set, lower or order urgency |
| Show what is missing and ask for it | Treat missing data as normal |
| Record consent per purpose and honour withdrawal | Store audio, or use real patient data in this prototype |
| Keep an append-only, hash-chained audit trail | Claim DPDP compliance, CDSCO clearance or clinical accuracy |

**Accountability.** Clinical decisions belong to the clinician who signs off. The project team is accountable for the
software: its rules, its limits, and telling users plainly what it cannot do.

## 2. Principles and how the code meets them

Drawn from the ICMR Ethical Guidelines for AI in Biomedical Research and Healthcare (2023), WHO *Ethics and governance
of AI for health* (2021) and India's AI Governance Guidelines (MeitY, 2025).

| Principle | What SEHAT does | Where | Gap |
|:---|:---|:---|:---|
| **Autonomy and consent** | Consent per purpose (`triage`, `ai_assist`, `voice_cloud`, `link_visits`); AI assistance can be refused and triage still works without it; withdrawal stops further processing and discards in-flight results | `backend/app/consent.py`, `privacy/gateway.py` | Hindi and Odia notices are unreviewed drafts; no flow yet for a child's guardian or an assisted decision |
| **Safety and risk minimisation** | Rules-first urgency, raise-only model input, GREEN must be earned, RED review target, fail-closed configuration | `GUARDRAILS.md` §1 | No clinical validation; RED escalation is on-screen only |
| **Human oversight and accountability** | Named sign-off, override with reason code, per-field review of every AI value, audit of every action | `backend/app/review_queue.py`, `ai/review.py`, `audit.py` | Shared demo accounts cannot prove who acted (demo only) |
| **Transparency and explainability** | Every value links to its source; counterfactuals; keyword suggestions labelled as rules, not AI; model card with measured limits | `GUARDRAILS.md`, `MODEL_CARD.md` | Protocol passages behind each rule are not shown yet (Phase 4) |
| **Privacy and data protection** | PII redacted before any model; audio never stored; synthetic data only; cloud off unless explicitly enabled with a synthetic-data acknowledgement | `backend/app/privacy/`, `config.py` | Redaction is heuristic; erasure and retention enforcement not built |
| **Data quality and validity** | Schema checks, grounding, MAKER voting, OCR self-verification, evaluations reported with denominators | `backend/app/ai/`, `ocr/` | Synthetic, author-labelled data only; no real-document OCR evaluation |
| **Fairness and inclusion** | Hindi and Odia intake, voice with read-back, body map for low literacy, offline operation on one laptop | `frontend/`, `backend/app/voice/` | No bias evaluation across language, age, sex or literacy; PII detection is weak on Indian names |
| **Accessibility** | Voice input, spoken read-back where a voice exists, local languages, large touch targets | `docs/05`, `docs/12` | No formal accessibility audit |
| **Reliability** | CI on every change, skip gate on safety suites, pinned and hash-checked models | `.github/workflows/ci.yml` | One host, one SQLite database |

## 3. Indian law and guidance

| Framework | Status (12 Nov 2026) | What it asks | SEHAT's position | Gaps before real patients |
|:---|:---|:---|:---|:---|
| **Digital Personal Data Protection Act 2023 and Rules 2025** | Rules notified 14 Nov 2025; most duties apply about 18 months later (around May 2027); consent-manager provisions earlier (*verify dates*) | Notice and consent by purpose, data-principal rights (access, correction, erasure, grievance, nomination), verifiable parental consent for children, no tracking of children, security safeguards, breach notice to the Board and to people affected; medical emergency is a legitimate use without consent | **"DPDP-ready by design"**, never "compliant": purpose-based consent, versioned notice, withdrawal, minimisation | Erasure, grievance officer and flow, nomination, breach procedure, children's verifiable consent, retention enforcement, DPIA |
| **IT Act 2000 s.43A and SPDI Rules 2011** | In force until the DPDP provisions replace them (*verify commencement*) | Health information is sensitive personal data; consent for collection; reasonable security practices | Synthetic data only; security controls in `SECURITY.md` | A documented security programme (for example ISO/IEC 27001-style) |
| **Telemedicine Practice Guidelines 2020** (Board of Governors in supersession of MCI, 25 Mar 2020) | In force (*verify any later amendment*) | AI and decision-support tools may assist the registered medical practitioner; AI may not counsel patients or prescribe; the final call is the practitioner's | Non-diagnostic output guard; named clinician sign-off; no prescription or counselling text | The repo cites the clause as "TPG 5.4": confirm the section number against the published text |
| **ICMR Ethical Guidelines for AI in Biomedical Research and Healthcare (2023)** | In force; a living document | Principles including autonomy, safety, trustworthiness, data privacy, accountability, fairness and validity; ethics committee review; validation before deployment | Principles mapped in §2 | Ethics committee approval and a validation study before any real data |
| **Medical Devices Rules 2017 and CDSCO medical device software guidance** | Draft guidance October 2025; reported finalised in 2026 (*verify on cdsco.gov.in*) | Software intended for clinical decision support is generally a medical device (SaMD), classified by risk, licensed before clinical use | **"Research prototype, not a clinically validated device"**; no clearance claimed | Intended-use statement, risk classification, quality system, clinical evidence |
| **India AI Governance Guidelines (MeitY, Nov 2025)** | Voluntary | Seven principles ("sutras") including trust, people first, fairness, accountability, understandable by design, safety and resilience | Human oversight, transparency, fail-closed design | A published incident and grievance channel |
| **Strategy for AI in Healthcare for India (SAHI) and BODH** (MoHFW, Feb 2026) | National direction | Responsible health AI; BODH as a benchmarking platform for health AI | Fits the rules-first, human-reviewed approach | Independent benchmarking (BODH is a candidate route) |
| **Ayushman Bharat Digital Mission (ABHA) and FHIR R4** | Standards in use nationally | Interoperable, consented records | ABHA numbers are redacted before any model; FHIR R4 export is planned (Phase 2) and not validated | Consent artefacts through the ABDM framework if integrated |
| **Charter of Patients' Rights** (drafted by NHRC; released by MoHFW 2018; states asked to adopt, 2019) | Advisory; state adoption is partial | Rights to information, records, confidentiality and dignity, informed consent, second opinion, grievance redressal | See §5 | Grievance redressal |

## 4. International references (context, not binding in India)

| Reference | Why it matters here |
|:---|:---|
| WHO, *Ethics and governance of AI for health* (2021) and guidance on large multi-modal models (Jan 2024) | Six principles (autonomy, well-being and safety, transparency, accountability, inclusiveness, sustainability); warns about automation bias, which is why every AI value is reviewed field by field |
| EU AI Act (Regulation 2024/1689), as amended by the 2026 Digital Omnibus | AI in medical devices is high-risk; application dates for high-risk duties were moved later (*verify*) |
| EU GDPR, Art. 9 and Art. 22 | Health data is a special category; people have rights against solely automated decisions with significant effects. SEHAT keeps a human decision-maker |
| US FDA clinical decision support guidance (revised Jan 2026) | Software stays non-device CDS only if a clinician can independently review the basis of a recommendation; SEHAT's source links, and the planned evidence panel, serve exactly that |
| UNESCO Recommendation on the Ethics of AI (2021); OECD AI Principles | Human rights, fairness and accountability baselines |
| ISO 14971, IEC 62304, IEC 82304-1, ISO/IEC 42001, ISO/IEC 23894, NIST AI RMF 1.0 | Risk management, software life cycle, AI management and AI risk standards to align with later. **None is claimed today** |

## 5. Patient rights in SEHAT today

| Right | How it works today | Status |
|:---|:---|:---|
| To be told, in their language, what happens to their data | Versioned consent notice served by the backend; Hindi and Odia drafts carry a warning until reviewed | Partial |
| To agree per purpose, and to refuse AI | Separate consent for triage, AI assistance, cloud speech and linking visits; omitted means declined | Built |
| To withdraw | Withdrawal stops further processing; work in flight is discarded | Built |
| To know when AI was involved | AI values are marked and reviewed; keyword suggestions are labelled as rules | Built |
| To a human decision | A named clinician signs off every case | Built |
| To see and correct their record | Reviewers correct values with reasons; patients hold a SEHAT code for their own visits | Partial |
| To erasure | Not built | Gap |
| To a grievance route and a named officer | Not built | Gap |
| To nominate someone (DPDP) | Not built | Gap |
| Children: a guardian's verifiable consent | Not built | Gap |
| Emergencies: care never waits for SEHAT | Staff follow normal emergency care; an emergency bypass in the software is not built | Gap |

## 6. Before any real patient (all required)

1. Institutional Ethics Committee approval of a written protocol (ICMR 2023).
2. A CDSCO intended-use statement and risk classification, and the licensing that follows.
3. A clinical validation study with pre-agreed acceptance criteria.
4. Native-speaker and clinical review of every Hindi and Odia text (`docs/13`).
5. The real-document OCR evaluation (`docs/15`).
6. A data protection impact assessment; agreements with any cloud vendor, or cloud switched off.
7. Real identity (OIDC), no shared demo accounts, a security review and penetration test.
8. Erasure, grievance, breach response and retention enforcement.
9. A bias evaluation across language, age, sex and literacy.
10. Staff training on automation bias: the screen supports a decision, it does not make one.

## 7. What we never claim

From `AGENTS.md`: no diagnostic capability; no DPDP compliance ("DPDP-ready by design"); no CDSCO clearance
("research prototype, not a clinically validated device"); no accuracy claim without its denominator and dataset; no
comparison against doctors.
