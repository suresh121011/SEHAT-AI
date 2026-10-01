# SEHAT-AI

SEHAT AI is a **rules-first, non-diagnostic, multimodal clinical triage and handoff engine** for Indian healthcare facilities.

Built on **Microsoft Semantic Kernel** with **Cognizant TriZetto AI Gateway** alignment.

---

## Overview

| Principle | Description |
|:---|:---|
| **Rules-First** | Deterministic AIIMS protocol rules set urgency. The LLM only extracts and summarises — it may raise urgency, never lower it. |
| **Non-Diagnostic** | SEHAT AI is explicitly non-diagnostic (TPG 5.4, ICMR 2023). It assists triage, not diagnosis. |
| **Multimodal** | Handles vitals, audio notes, clinical documents, and visual signs. |
| **Human Sign-Off** | A named human reviewer always signs off every triage. Every field links to its source. |
| **DPDP-Ready** | Designed with patient privacy, data minimisation, and consent boundaries aligned to India's DPDP Act. |
| **FHIR R4** | FHIR R4-shaped export contracts planned for EHR/EMR integration (not yet validated against a FHIR server). |

---

## Documentation

| Document | Purpose |
|:---|:---|
| [`docs/01_PRD.md`](docs/01_PRD.md) | Product Requirements Document |
| [`docs/02_Features_Checklist.md`](docs/02_Features_Checklist.md) | Prioritised features with evaluation weight mapping |
| [`docs/03_Technical_Architecture.md`](docs/03_Technical_Architecture.md) | 6-layer pipeline and tech stack |
| [`docs/04_Security_Privacy_Access.md`](docs/04_Security_Privacy_Access.md) | Security, privacy, consent, DPDP-ready design |
| [`docs/05_Frontend_Specification.md`](docs/05_Frontend_Specification.md) | UI/UX screens, flows, components |
| [`docs/06_API_Data_Contracts.md`](docs/06_API_Data_Contracts.md) | API endpoints, JSON schemas, FHIR export |
| [`docs/07_Demo_Script.md`](docs/07_Demo_Script.md) | 5-minute Odisha-focused demo script |
| [`docs/08_Implementation_28h_Plan.md`](docs/08_Implementation_28h_Plan.md) | Phased 28-hour implementation plan |
| [`docs/09_Implementation_Todo_List.md`](docs/09_Implementation_Todo_List.md) | Granular checkbox task list for each phase |
| [`docs/10_Safety_Rules_Engine.md`](docs/10_Safety_Rules_Engine.md) | Deterministic triage rules engine: clinical sources, decisions, tests |
| [`docs/11_Privacy_Consent_Audit.md`](docs/11_Privacy_Consent_Audit.md) | Consent, PII redaction, AI gateway, audit log — implemented controls, threat model, limitations (demo-only) |
| [`docs/14_OCR_Pipeline.md`](docs/14_OCR_Pipeline.md) | OCR pipeline (Phase 5): on-device Surya/PaddleOCR + Chandra, Gödel verification, RxNorm, sourced reference ranges, source-linked review |

---

## Disclaimer

> This is a **research prototype, not a clinically validated device**. It has not received CDSCO clearance.
> Verify all clinical rules against current primary sources before implementation.
>
> **Demo accounts only — not for real patients.** Login uses shared, password-less demo accounts; the backend refuses to start outside `development`/`test`. PII redaction is a heuristic safeguard, not anonymization; the audit log is tamper-evident, not immutable. See [`docs/11_Privacy_Consent_Audit.md`](docs/11_Privacy_Consent_Audit.md).
