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
| [`docs/12_Voice_Pipeline.md`](docs/12_Voice_Pipeline.md) | Voice pipeline: technology decisions, consent, read-back policy, verification status |
| [`docs/13_Language_Review_Checklist.md`](docs/13_Language_Review_Checklist.md) | Hindi and Odia wording awaiting native-speaker and clinical review |
| [`docs/14_OCR_Pipeline.md`](docs/14_OCR_Pipeline.md) | OCR pipeline (Phase 5): on-device Surya/PaddleOCR + Chandra, Gödel verification, RxNorm, sourced reference ranges, source-linked review |
| [`docs/15_OCR_Evaluation_Plan.md`](docs/15_OCR_Evaluation_Plan.md) | Governed real-document OCR evaluation plan (not started; required before real patient data): governance, sampling, annotation, metrics, proposed acceptance criteria |
| [`docs/16_LLM_Extraction_MAKER.md`](docs/16_LLM_Extraction_MAKER.md) | LLM extraction: providers, local model, NeMo Guardrails, red-flag keywords, grounding, MAKER voting, measured results |
| [`docs/17_Reviewer_Dashboard.md`](docs/17_Reviewer_Dashboard.md) | Reviewer dashboard: rules-ordered queue, sign-off and override, RED review target |
| [`docs/18_Medical_Image_Pipeline.md`](docs/18_Medical_Image_Pipeline.md) | Medical image descriptions (never findings): backends, gates, local MedGemma, limits |
| [`docs/19_Encounter_Summary.md`](docs/19_Encounter_Summary.md) | Deterministic, source-linked encounter summary and its synthetic evaluation |
| [`docs/20_Agent_Harness_and_Governance_Files.md`](docs/20_Agent_Harness_and_Governance_Files.md) | What each governance and agent file contains, who reads it and why |
| [`docs/21_Referral_Closure_Tracking.md`](docs/21_Referral_Closure_Tracking.md) | Referral closure tracking (Phase 2): signed-off referral packet, append-only forward-only status to closure, 48-hour overdue flag, printable letter, FHIR R4 export, supervisor metadata only, limits |
| [`docs/22_Lab_Timeline.md`](docs/22_Lab_Timeline.md) | Lab timeline (Phase 3): checked values across reports and linked visits, day-first report dates, higher/lower/same only when comparable, named gaps, no urgency, limits |
| [`docs/23_Protocol_Evidence.md`](docs/23_Protocol_Evidence.md) | Protocol evidence (Phase 4): the cited source and curated passage behind each fired rule, looked up by rule id; evidence gate (hash, source, licence, thresholds, named reviewer); locations only until passages are checked; closed-vocabulary search; no urgency, limits |

---

## Quick start

```bash
python3.11 -m venv .venv && make setup && make doctor
```

Copy `.env.example` to `.env` and fill in your own values, then run `make dev-backend` and `make dev-frontend` in two
terminals and open `http://127.0.0.1:3010`. `make test` runs every suite. [`RUNBOOK.md`](RUNBOOK.md) has the details.

## Governance and agent files

| File | Purpose |
|:---|:---|
| [`GUARDRAILS.md`](GUARDRAILS.md) | Every safety control: what it does, where it lives, which test pins it, its limits; built vs planned |
| [`ETHICS.md`](ETHICS.md) | Principles, Indian and international frameworks, patient rights, what SEHAT does not claim |
| [`MODEL_CARD.md`](MODEL_CARD.md) | Every model and engine: pin, licence, intended use, measured results with denominators, failure modes |
| [`SECURITY.md`](SECURITY.md) | Reporting a vulnerability; controls and known limits |
| [`RUNBOOK.md`](RUNBOOK.md) | Setup, run, test, demo, troubleshooting, test baseline, roadmap |
| [`AGENTS.md`](AGENTS.md), [`CLAUDE.md`](CLAUDE.md), [`SKILLS.md`](SKILLS.md), [`HARNESS.md`](HARNESS.md) | Rules, skills and enforcement for AI coding agents working in this repo |

---

## Disclaimer

> This is a **research prototype, not a clinically validated device**. It has not received CDSCO clearance.
> Verify all clinical rules against current primary sources before implementation.
>
> **Demo accounts only — not for real patients.** Login uses shared, password-less demo accounts; the backend refuses to start outside `development`/`test`. PII redaction is a heuristic safeguard, not anonymization; the audit log is tamper-evident, not immutable. See [`docs/11_Privacy_Consent_Audit.md`](docs/11_Privacy_Consent_Audit.md).
