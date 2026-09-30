# SEHAT AI — Agent Instructions

> **Single Source of Truth:** [`docs/sehat_ai_final_architecture__2.md`](docs/sehat_ai_final_architecture__2.md)

## Project Overview

SEHAT AI is a **rules-first, non-diagnostic, multimodal clinical triage and handoff engine** for Indian healthcare facilities. Built on Microsoft Semantic Kernel with Cognizant TriZetto AI Gateway alignment.

**Core Philosophy:** Deterministic AIIMS protocol rules set urgency. The LLM only extracts and summarises. A named human reviewer always signs off. Every field links to its source. Every referral is tracked to closure.

## Documentation Set

All project documentation lives in `docs/`:

| Document | Purpose |
|:---|:---|
| [`docs/01_PRD.md`](docs/01_PRD.md) | Product Requirements Document |
| [`docs/02_Features_Checklist.md`](docs/02_Features_Checklist.md) | Prioritised features with evaluation weight mapping |
| [`docs/03_Technical_Architecture.md`](docs/03_Technical_Architecture.md) | Condensed 6-layer pipeline and tech stack |
| [`docs/04_Security_Privacy_Access.md`](docs/04_Security_Privacy_Access.md) | Security, privacy, consent, DPDP-ready design |
| [`docs/05_Frontend_Specification.md`](docs/05_Frontend_Specification.md) | UI/UX screens, flows, components |
| [`docs/06_API_Data_Contracts.md`](docs/06_API_Data_Contracts.md) | API endpoints, JSON schemas, FHIR export |
| [`docs/07_Demo_Script.md`](docs/07_Demo_Script.md) | 5-minute Odisha-focused demo script |
| [`docs/08_Implementation_28h_Plan.md`](docs/08_Implementation_28h_Plan.md) | Phased 28-hour implementation plan |
| [`docs/09_Implementation_Todo_List.md`](docs/09_Implementation_Todo_List.md) | Granular checkbox tasks for each phase |
| [`docs/10_Safety_Rules_Engine.md`](docs/10_Safety_Rules_Engine.md) | Deterministic triage rules engine: verified clinical sources, decision record, test matrix |

## Agent Rules

1. **Never invent features** not in the architecture document.
2. **Never claim diagnostic capability.** SEHAT AI is explicitly non-diagnostic (TPG 5.4, ICMR 2023).
3. **Never claim DPDP compliance.** Say "DPDP-ready by design." Substantive duties start May 2027.
4. **Never claim CDSCO clearance.** This is a "research prototype, not a clinically validated device."
5. **Rules-first always.** The LLM may only RAISE urgency, never lower it. Missing vitals = "needs human review", never "normal."
6. **Cognizant alignment.** Reference TriZetto AI Gateway and Microsoft Semantic Kernel where applicable.
7. **Source-linked evidence.** Every extracted field must trace to its source (audio timestamp, OCR bounding box, manual entry).
8. Verify clinical rules against current primary sources before implementation. The architecture document is a design spec, not a clinical protocol.
