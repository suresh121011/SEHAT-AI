# SEHAT AI — Safety Rules Engine (Phase 2)

> **Version:** engine 1.0.0 · ruleset 1.0.0 | **Date:** 2026-09-30
> **Code:** `backend/app/rules/` · **API:** `POST /api/v1/triage/process` (roles: `anm`, `medical_officer`)
> **Status:** Research prototype, not a clinically validated device. Non-diagnostic. A named human reviewer signs off every triage.

---

## 1. Why a deterministic engine

Urgency is a safety decision. It must be reproducible, explainable without a model, and traceable to a published source. The engine is pure Python and has no LLM, network, database or randomness; a test enforces this (`tests/rules/test_isolation.py`). The LLM layer (Phase 6) and JEV can only **raise** the result through `enforce_raise_only()`.

```
TriageInput ──► validate ──► derive ──► ATP rules ──► scenario rules ──► NEWS2 ──► qSOFA
                (Pydantic)   (shock                   (raise-only)       │         │
                             index)                                     ▼         ▼
                                               completeness gate ──► aggregate: max(RED > YELLOW > GREEN)
                                                                               │
                                                             TriageResult (deterministic, explained)
                                                                               │
                                               LLM / JEV suggestion ──► enforce_raise_only() ──► final urgency
```

## 2. How urgency is calculated

1. **Validate.** Units are fixed (°C, mmHg, /min, %). Impossible or contradictory values are rejected with a 400 `VALIDATION_ERROR` and never coerced. Examples: SpO2 >100, negative RR, DBP ≥ SBP, consciousness "A" with GCS <15, unknown flag or scenario, data blocks that belong to a different scenario, booleans or strings where numbers are expected, and NaN. Temperature is not rounded.
2. **ATP RED rules** run in every scenario and at every age. They can only escalate.
3. **Scenario urgency rules** can only raise urgency. Scenario **advisories** are a separate list and never affect urgency.
4. **NEWS2** runs only for age ≥16, when not pregnant, and in a scenario that uses it. Missing parameters are never assumed normal. The result is `incomplete`, with a lower-bound `partial_total` that can still escalate.
5. **qSOFA** runs only for age ≥16 with `suspected_infection`. With missing criteria it can still be positive, but a negative is never concluded.
6. **Completeness gate:** GREEN must be earned. If age, the red-flag screen, a core vital, NEWS2 oxygen status, or a scenario-required field is missing, or if age is under 14, urgency is at least **YELLOW** with `needs_human_review: true` and `determination` set to `insufficient_data` or `outside_validated_population`.
7. **Aggregate.** `urgency = max(every triggered rule)`. There is no GREEN rule; GREEN is only the floor.

### ATP RED criteria implemented (ATP_2022, Supplementary Table 1)

| Rule ID | Criterion |
|:---|:---|
| `ATP_RED_RR` | RR >22 or <10 /min |
| `ATP_RED_SPO2` | SpO2 <90% |
| `ATP_RED_PULSE` | Pulse <50, or >120 without fever. ATP gives no numeric fever cut-off; SEHAT uses >38.0 °C, the NEWS2 normal upper bound, and treats unknown temperature as afebrile so the rule still fires. |
| `ATP_RED_BP_HIGH` / `ATP_RED_BP_LOW` | SBP >220 or DBP >110 / SBP <90 or DBP <60, on any recorded reading |
| `ATP_RED_SHOCK_INDEX` | Pulse ÷ SBP >1 |
| `ATP_RED_SENSORIUM` | Responds only to Voice or Pain, or Unresponsive (ACVPU V/P/U) |
| `ATP_RED_FEVER_39` | Fever with temperature >39 °C |
| `ATP_RED_<FLAG>` | One rule per `AtpFlag`, covering every remaining row: stridor, facial angioedema, active seizure, incomplete sentences, audible wheeze, active bleeding, chest pain <24 h, limb weakness <24 h, suspected stroke <24 h, dangerous-mechanism trauma, SOB <12 h, limb ischaemia <48 h, allergic reaction, scrotal pain (young male), severe pain, sudden abdominal pain, sudden headache, urinary retention, fever + immunocompromise, time-sensitive outside evaluation, syncope, needle-prick, abdominal pain + vaginal bleeding, agitated/violent, poisoning/envenomation, 3rd-trimester pain or bleeding |
| `ATP_RED_THIRD_TRIMESTER_BLEEDING_DERIVED` | Trimester 3 + maternal danger sign "vaginal bleeding" (derived, so a bleed is RED even if only the danger sign was ticked) |

"Triage officer discretion" is not computable. It is the human reviewer override (Phase 8), not a rule.

### Score → urgency mapping (SEHAT_POLICY, based on RCP Chart 2)

| Score | RCP clinical risk | SEHAT urgency |
|:---|:---|:---|
| NEWS2 ≥7 | High: urgent or emergency response | RED |
| NEWS2 5–6 | Medium: key threshold for urgent response | YELLOW |
| Any single NEWS2 parameter = 3 | Low–medium: urgent ward-based response | YELLOW |
| NEWS2 0–4 | Low | no escalation |
| qSOFA ≥2 (suspected infection) | Positive screen, **not a diagnosis** | YELLOW (ATP rules already make most qSOFA-positive patients RED) |

### Scenario packs

| Scenario | Urgency rules (raise-only) | Advisories (never urgency) | Extra fields required for GREEN |
|:---|:---|:---|:---|
| `opd` | none beyond ATP + NEWS2 | none | none |
| `maternal` | Any MoHFW danger sign → YELLOW (MOHFW_MCP). Hb <7.0 g/dL → YELLOW (WHO_HB_2024). RED via ATP (seizure, 3rd-trimester bleed or pain, BP >220/110, active bleeding). NEWS2 not used in pregnancy. | Maternal age <18 or >35 (pending clinical verification) | `maternal.danger_sign_screen_completed` |
| `chronic_ncd` | Any reading SBP >180 or DBP >110 → YELLOW (IHCI_HTN). >220/110 is RED via ATP. | none | ≥2 `bp_readings` |
| `health_camp` | none | CBAC >4 → prioritise NCD screening (NPCDCS_CBAC) | none |
| `campus_fever` | none (temp >39 °C is RED via ATP; qSOFA if infection suspected) | Meets WHO ILI definition (WHO_ILI_2014) | none |
| `occupational` | none | Standard threshold shift ≥10 dB (OSHA_1910_95, a US definition) | none |
| `referral` | none | Referral packet incomplete (escort, transport, identity, consent) | none |

## 3. Output and explainability

`TriageResult` answers "why is this patient RED?" with no model involved. Each `triggered_rules[]` entry has:
- `rule_id`, `family`, `urgency`, `reason`
- `source_id` and the full `source` citation
- `evidence: {field: {value, threshold}}`

The result also carries:
- `scores` with the NEWS2 and qSOFA components
- `missing_fields`
- `advisories`
- `engine_version`, `ruleset_version`, `evaluated_at`

The result contains no patient identifiers or free text, so it is safe to put in an audit event.

## 4. LLM boundary

`enforce_raise_only(result, suggested)` returns `{deterministic_urgency, suggested_urgency, final_urgency, override_applied, override_reason}` with `final = max(deterministic, suggested)`.
- A downgrade is refused and recorded: *"LLM cannot downgrade deterministic safety classification"*.
- The deterministic result is never mutated.
- The YELLOW insufficient-data floor cannot be lowered either.

## 5. Versioning and changing rules

- Rules are frozen Python dataclasses (`app/rules/registry.py`). Each one has an ID, family, urgency, reason, one `source_id` and a predicate. They are not stored in YAML: predicates need code, and a DSL loader adds failure modes.
- **Adding a rule:**
  1. Add the rule to `atp.py` or a scenario pack.
  2. Cite a registered source in `sources.py`.
  3. Add boundary tests.
  4. Bump `RULESET_VERSION` in `engine.py`.
- `test_isolation.py` fails if a rule cites an unregistered source, duplicates an ID, or if `app/rules` imports LLM, network or storage code.

## 6. Clinical sources

| ID | Source | Used for | Accessed |
|:---|:---|:---|:---|
| `ATP_2022` | Singh SK, Sahu AK, et al. *J Emerg Trauma Shock* 2022;15(3):124-7, **Supplementary Table 1**. doi:10.4103/jets.jets_146_21, PMC9639733 | ATP RED criteria; validation population ≥14 years | 2026-09-30 |
| `ATP_2020` | Sahu AK, Bhoi S, et al. *J Emerg Trauma Shock* 2020;13(2):107-9. doi:10.4103/JETS.JETS_137_19 | Qualitative Yellow/Green definitions | 2026-09-30 |
| `RCP_NEWS2_2017` | Royal College of Physicians, NEWS2 (2017), Chart 1 and Chart 2 | NEWS2 bands (Scale 1/2, ACVPU) and clinical-risk thresholds; not for age <16 or pregnancy | 2026-09-30 |
| `SEPSIS3_2016` | Singer M, et al. *JAMA* 2016;315(8):801-10. doi:10.1001/jama.2016.0287 | qSOFA; "not a stand-alone definition of sepsis" | 2026-09-30 |
| `WHO_HB_2024` | WHO Guideline on haemoglobin cutoffs to define anaemia (2024) | Severe anaemia in pregnancy: Hb <70 g/L | 2026-09-30 |
| `MOHFW_MCP` | MoHFW/MoWCD Mother & Child Protection Card; NHSRC MPW (Female) guidebook | Pregnancy danger signs → refer immediately | 2026-09-30 |
| `IHCI_HTN` | India Hypertension Control Initiative protocol; NHM STG Hypertension | BP >180/110 → assess target-organ damage, refer immediately | 2026-09-30 |
| `NPCDCS_CBAC` | NPCDCS Community Based Assessment Checklist | CBAC >4 → prioritise NCD screening | 2026-09-30 |
| `WHO_ILI_2014` | WHO ILI/SARI surveillance case definitions (2014) | ILI: fever ≥38 °C + cough, onset ≤10 days | 2026-09-30 |
| `OSHA_1910_95` | 29 CFR 1910.95(g)(10) (US; no Indian numeric equivalent located) | Standard threshold shift | 2026-09-30 |
| `SEHAT_POLICY` | This document, §7 | Project conventions (score mappings, safety floor, fever cut-off) | n/a |

## 7. Decision record

| # | Decision | Why |
|:---|:---|:---|
| ADR-1 | Deterministic rules are separate from the LLM | Safety decisions must be reproducible and explainable without a model. The engine also serves as the rules-only "Lite" fallback. |
| ADR-2 | The LLM (and JEV) can only raise urgency | The cost of under-triage far exceeds that of over-triage. The rules' minimum urgency is authoritative. |
| ADR-3 | Every threshold is source-controlled | One registry (`sources.py`) holds the citations. Every rule cites exactly one source, and project conventions cite `SEHAT_POLICY` rather than a paper. |
| ADR-4 | GREEN must be earned | A rule that doesn't fire because data is missing looks the same as a healthy patient, so without a gate the engine would fail open. GREEN therefore needs age ≥14, a completed red-flag screen, all core vitals, and scenario-required fields. Otherwise urgency is YELLOW with `needs_human_review`. This keeps the 3-value enum used by the DB, FHIR and UI. |
| ADR-5 | **Project docs corrected to ATP 2022** | The earlier docs (arch §11, doc 03 §6, docs 08/09) listed RR >30/<8, pulse >130, GCS <13, SBP <90 and "temp <35 → RED", citing PubMed 36353399. The published Supplementary Table 1 says RR >22/<10, pulse <50/>120 without fever, SBP/DBP limits both ways, shock index >1 and AVPU. The earlier numbers would have missed RED patients (for example RR 23–30 and pulse 121–130). "Temp <35 → RED" has no source; hypothermia ≤35.0 °C now scores NEWS2 3, which is YELLOW. |
| ADR-6 | NEWS2 table completed from RCP Chart 1 | The docs' table was missing pulse 111–130 = 2, temperature 38.1–39.0 = 1 and every SpO2 band. The doc mapping "≥5 YELLOW / ≥7 RED" is kept and extended with RCP's single-parameter-3 trigger → YELLOW. |
| ADR-7 | Dengue pack removed from Phase 2 | Dengue is not one of the 7 scenarios. "Platelets <100K → RED (WHO 2009)" is mis-cited: WHO 2009 severe dengue is defined by plasma leakage, severe bleeding and organ impairment, not by platelet count. **Impact:** the Odisha demo (doc 07, doc 05 triage card, doc 06 example) relies on that rule. The demo patient is still RED through ATP when "severe pain" or "sudden abdominal pain" is recorded. A verified WHO 2009 warning-signs pack is a follow-up. |
| ADR-8 | YELLOW has no invented ATP list | ATP defines only RED. YELLOW comes from sourced scores (NEWS2, qSOFA), scenario rules, and the safety floor, each labelled with its source. |
| ADR-9 | Maternal danger signs map to at least YELLOW | MoHFW says "refer immediately" but gives no urgency tier. The signs that ATP classes as RED become RED through ATP. |
| ADR-10 | Advisories never affect urgency | CBAC, ILI, hearing shift and referral completeness are screening or process signals, not triage acuity. |
| ADR-11 | Synthetic vignettes only | No real patient data in tests. Boundaries are tested on both sides of each threshold. |
| ADR-13 | Hardening from the independent safety review | Temperature is no longer rounded (39.04 was hiding ATP >39). "Alert" with GCS <15 is rejected. The SpO2 scale is shown in NEWS2 evidence, and Scale 2 raises an advisory. A pregnant patient outside the `maternal` scenario can't be GREEN without the danger-sign screen. Clinical inputs are parsed strictly (no bool/str/float coercion). |
| ADR-12 | Children <14 and pregnancy | ATP was validated at age ≥14 and NEWS2 is for ≥16 and non-pregnant patients. Red flags still escalate at any age, but GREEN is never assigned below 14 (`outside_validated_population`). No paediatric thresholds are invented. |

## 8. Test matrix (`backend/tests/rules/`, 230 tests)

| Category | Example | Expected |
|:---|:---|:---|
| ATP boundaries | RR 22 / 23, SpO2 90 / 89, pulse 120 / 121 (afebrile, febrile, unknown temperature), SBP 220 / 221, DBP 60 / 59, SI 1.0 / 1.01, A / C / V | RED only past the threshold |
| Every ATP flag | each `AtpFlag` | RED with source and evidence |
| Multiple red flags | stridor + chest pain + SpO2 85 | RED, all listed |
| NEWS2 bands | every boundary of every parameter, Scale 1 and 2, oxygen | RCP points |
| NEWS2 mapping | total 7 without an ATP trigger / 5 / single 3 / 0 | RED / YELLOW / YELLOW / GREEN |
| qSOFA | RR 21 / 22, SBP 100 / 101, mentation; missing criteria | positive at ≥2; a negative is never concluded when incomplete |
| Missing data | no screen, no age, missing SpO2, consciousness or O2 status | YELLOW + `insufficient_data`, never GREEN |
| Conflicts | NEWS2 YELLOW + ATP RED | RED |
| Population | age 10; age 15; pregnancy | not GREEN; ATP without NEWS2; NEWS2 not applicable |
| Override | RED+GREEN, RED+YELLOW, YELLOW+GREEN, GREEN+YELLOW, GREEN+RED, none | RED, RED, YELLOW, YELLOW, RED, deterministic |
| Scenarios | each pack: one urgency vignette and one advisory vignette | as in §2 |
| Invalid input | SpO2 140, RR −1, DBP ≥ SBP, A + GCS 8, `spo2: true`, `"95"`, NaN, unknown flag or scenario, wrong scenario block | 400 `VALIDATION_ERROR` |
| Review regressions | temp 39.04 → RED; Scale 2 visible; pregnant in `opd` → not GREEN | as stated |
| Isolation | imports in `app/rules` | no LLM, network, DB or random |
| API | MO / ANM allowed; patient 403; anonymous 401 | as stated |

## 9. Known limitations

- The thresholds come from published protocols but have **not been reviewed by a clinician for SEHAT's settings**. A clinical sign-off is required before any real use.
- There are no paediatric thresholds; children <14 always go to human review.
- `SEHAT_POLICY` mappings (score → colour, fever cut-off, maternal YELLOW floor, safety floor) are project conventions.
- The maternal age advisory is taken from the architecture doc and still needs a primary source.
- The occupational STS definition is from US OSHA.
- Imaging and lab signals (ST elevation, troponin) arrive with the Phase 5 OCR/imaging pipeline.
- Results are not yet persisted to `triage_notes`. That happens with the intake flow.
