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
| **FHIR R4** | Interoperable data contracts for seamless EHR/EMR integration. |

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

---

## Disclaimer

> This is a **research prototype, not a clinically validated device**. It has not received CDSCO clearance.
> Verify all clinical rules against current primary sources before implementation.
