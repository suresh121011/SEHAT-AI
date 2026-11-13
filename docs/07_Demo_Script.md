# SEHAT AI — Demo Script (5 Minutes)

> **Version:** 2.0 | **Date:** 2026-11-13 (Phase 7; replaces v1.1 of 2026-09-30). Aligned to `main` after Phases 1–6.
> **Setting:** Odisha PHC — fever with severe abdominal pain (a dengue-like picture, as clinical context only).
> **Status:** Research prototype, not a clinically validated device. Every patient in the demo is a **synthetic,
> constructed teaching case**.

> **What decides urgency:** the deterministic rules engine. Every urgency shown must match a `rule_id` the engine
> really returns; `make demo-check` checks the two that this script depends on. SEHAT AI does not diagnose dengue: no
> engine rule reads platelets, and a dengue warning-sign pack stays deferred until it can be built from verified
> NCVBDC 2023 / WHO criteria (docs/10 ADR-7).

> **Screen labels** below are those on `main` at `15544dd`. The customer-friendly UI redesign
> (`feat/customer-friendly-ui`) is not merged yet. If it lands, rehearse again and update the labels here.

---

## 1. Start the demo (one command)

From the repository root on the demo laptop, with the local model downloaded (`make llm-download`, once):

```bash
make demo
```

`make demo` (`backend/demo_kit/`) does five things:

1. Starts the local Qwen model, the backend and the frontend, all on 127.0.0.1 only.
2. Uses a **fresh scratch database** outside the repository, never `sehat.db`.
3. Runs **offline**: cloud AI, cloud speech recognition and cloud spoken read-back are switched off whatever `.env`
   says. Extraction runs on the local
   model with NeMo Guardrails and the red-flag keyword suggester on, and facility isolation stays on.
4. Seeds the cases below through the real API, with consent, rules engine, sign-off and audit.
5. Runs the pre-demo check and prints `Ready for the demo` or what to fix.

`Ctrl-C` stops everything. Open `http://127.0.0.1:3010` (not `localhost`).

| Variant | Command |
|:---|:---|
| Model server already running | `make demo NO_LLM=1` |
| Rehearse without the model (keyword stand-in, labelled as such) | `make demo DEMO_MODEL=fake` |
| With network, for the clinical assistant beat (Azure) | `make demo ONLINE=1` |
| Other ports | `make demo API_PORT=8020 WEB_PORT=3020` |
| Check a running stack again, 5 minutes before | `make demo-check` |

The backend reads the developer `.env` for what the kit does not set: OCR, voice and image engines. Run `make demo`
from the main checkout so those settings apply. `make demo-check` shows each one as on or off.

### Seeded cases (`backend/demo_kit/cases.py`, pinned by `backend/tests/demo_kit/`)

| Case | Rules result | Used in |
|:---|:---|:---|
| Fallback: the demo patient, already triaged | RED — `ATP_RED_SEVERE_PAIN` | Beats 4–6 if live intake fails |
| Same vitals, red-flag screen not done | YELLOW — `SAFETY_FLOOR_INSUFFICIENT_DATA` | Beat 4: GREEN must be earned |
| Breathless, SpO2 86 % on air | RED — `ATP_RED_RR`, `ATP_RED_SPO2` | Beat 5: RED first in the queue |
| Pregnant, headache with blurred vision | YELLOW — `MAT_YELLOW_DANGER_SIGN` | Beat 5: a scenario pack |
| Routine visit, all observations normal | GREEN | Beat 5: GREEN is earned |
| Chest pain, signed off and referred | RED — `ATP_RED_CHEST_PAIN_ACUTE_24H`; referral **On the way** | Beat 6: closure tracking |

### Checklist before going on stage

- [ ] The day before, run `make demo` once **with network**. The frontend fetches its web fonts the first time it
  compiles; offline it falls back to system fonts.
- [ ] `make demo` printed `Ready for the demo` (warnings read and understood)
- [ ] Wi-Fi off, notifications off, browser at 1280×800, zoom 100 %
- [ ] Synthetic lab report `backend/tests/fixtures/ocr/cbc_low_platelet.png` on the desktop
- [ ] Microphone tested (live recording needs `127.0.0.1` or HTTPS); the **▶ Sample** clips are the fallback
- [ ] Backup recording and screenshots on the desktop (see §4)
- [ ] Odia read-aloud: Chrome on the demo Mac has no Odia voice. The screen says so; the presenter reads the notice.

---

## 2. Script

### Beat 1: Consent (0:00–0:40)

> *"SEHAT AI is not an AI doctor. It organises what the patient says and brings, so a named clinician can decide
> faster. Everything you see runs on this laptop with Wi-Fi off."*

1. Sign in as **anm_demo** (health worker). Start a new case (scenario OPD) at PHC Khurda.
2. Consent screen in **Odia**. **Read aloud** says no Odia voice is available on this device, so read the notice to
   the patient. The Odia text is a **draft translation not yet reviewed by an Odia speaker**, and a banner says so.
3. The patient says *"haan"*. The health worker ticks the attestation. Consent is recorded per purpose; no audio is
   stored.

> *"Consent is per purpose and can be withdrawn. DPDP-ready by design, ahead of the duties that start in May 2027."*

### Beat 2: Voice with read-back (0:40–1:40)

> *"Speech recognisers get numbers wrong, and triage depends on numbers, so nothing heard is used until the health
> worker checks it."*

1. Choose **हिन्दी**, **On this device (local model)**. Play **▶ Sample: Hindi fever (synthetic)**.
2. The read-back card says what was heard (102 °F = 38.89 °C). The **health worker** taps **✓ Yes, that's right**.
   - 38.89 °C is not above the ATP threshold (>39 °C), so fever alone does not make this patient RED.
3. **Safety beat.** Play **▶ Sample: Hindi 'saadhe 39'**.
   - The card is flagged, because a word like *saadhe* changes the number. "Yes" is disabled.
   - The health worker enters 39.5 °C, which is labelled as entered by the health worker.
   - This clip only demonstrates the safety check. Do not carry 39.5 °C into the vitals: above 39 °C the ATP fever
     rule fires, and Beat 4 would no longer match this script.
4. Body map: head (headache) and abdomen (severe pain). Vitals: RR 20, SpO2 97 % on air, pulse 96, BP 118/76,
   temp 38.9 °C, alert.
5. Red-flag screen: tick **"Severe pain"** (the patient's severe abdominal pain).

> *"Anything doubtful — 'not', 'yesterday', 'saadhe' — cannot be accepted with one click. Voice only pre-fills; the
> rules engine and the health worker decide."*

Voice needs `VOICE_ENABLED` and the local speech model in `.env`. If `make demo-check` shows voice off, type the
complaint instead; the typed text goes through the same local-model extraction.

### Beat 3: The lab report (1:40–2:20)

1. Upload the synthetic CBC (`cbc_low_platelet.png`). OCR engines read it on the laptop.
2. Each value is highlighted on the page image. Platelets 85,000/µL are flagged below the lab's printed range and
   below the cited reference range. Hb 11.2 g/dL is also flagged below the printed range (12.0–15.0).
3. Say **"engines agree, checks passed — still needs your confirmation"**, never "verified". Say "engines agree"
   only if `make demo-check` showed both printed-text engines (PaddleOCR and Surya) ready. With one engine, say
   "read on this laptop — needs your confirmation". The health worker
   confirms each row. **Lab timeline** shows checked values across reports, with gaps named.

> *"Every value links back to the exact region of the page. Platelets are context for the doctor; no rule here reads
> them."*

If OCR is off in `make demo-check`, skip this beat and say so.

### Beat 4: The rules decide (2:20–3:10)

> *"This part is deterministic: no language model is involved in this decision."*

1. Submit triage. **🔴 RED.** **Why the rules gave …** shows `ATP_RED_SEVERE_PAIN` with its source, AIIMS Triage
   Protocol (*J Emerg Trauma Shock* 2022, Supplementary Table 1).
   - NEWS2 is 2 (low) and does not drive this RED. qSOFA is negative and is a screen, not a diagnosis.
2. **Protocol evidence for these rules** shows the source and the location in it as cited. No passage text is
   shown until a named reviewer checks it against the source (docs/23).
3. **What would change this result?** re-runs the rules with one input changed: without the severe-pain flag the
   result would be GREEN; without the red-flag screen, YELLOW with "needs human review". It explains the rules as
   implemented and is not clinical advice.
4. Open the seeded **"Same vitals, red-flag screen not done"** case. It is 🟡 YELLOW with "needs human review"
   (`SAFETY_FLOOR_INSUFFICIENT_DATA`): GREEN is never given on incomplete data.

> *"RED comes from one cited rule, not from an AI guess. The language model can only raise urgency, never lower it,
> and our safety scorecard checks that on 284 combinations."*

### Beat 5: The doctor reviews and signs (3:10–4:00)

1. Sign in as **mo_demo** (medical officer). The queue is ordered by the rules: the RED cases (the demo patient and
   the breathless patient) first, then YELLOW (including the pregnant patient with a danger sign), then GREEN.
2. Open the demo patient. Each value shows where it came from: the voice read-back, typed text, or the page region
   of the report. The summary note is built from these recorded facts, each line linked to its source.
3. Point at the banner, *"Research prototype, not a clinically validated device"*. Signed-off and reviewed items are
   labelled *"not a diagnosis"*. Machine-read values stay marked as pending review until a person confirms them.
4. **Sign off review…** records the sign-off under the signed-in account with its role and the server time (the demo
   accounts are shared; with OIDC sign-in, `AUTH_MODE=oidc`, each person signs in with their own account). **Record override** needs a reason code;
   the rules' original urgency is kept.
5. *(Online only — `make demo ONLINE=1`.)* The clinical assistant offers four fixed questions. It reads only this
   visit through read-only tools, and every sentence is checked against the recorded facts before it is shown. If
   offline, say: *"the assistant needs the cloud; in this offline room it is switched off, and it says so."*

> *"Security-relevant actions go into an append-only, hash-chained audit log: tamper-evident, not immutable."*

### Beat 6: Referral to closure (4:00–4:40)

1. From the signed-off demo patient, open **Referral**: District Hospital Bhubaneswar, 108 ambulance, escort, travel
   time, PM-JAY. The letter is built from the signed-off record.
2. Open the seeded **"Chest pain, signed off and referred"** case. Its status history reads **Referred → On the
   way**. Status only moves forward, and a referral with no update for 48 hours is flagged overdue.
3. Show the printable letter and the FHIR R4 export (structure checked by tests; not validated against a FHIR server).

> *"Most systems stop at the referral slip. We track whether the patient reached the hospital and was seen."*

### Beat 7: Close (4:40–5:00)

Slide: the safety scorecard (`make safety-eval`, docs/25). Every number comes with its denominator.

| On synthetic, author-labelled cases | Result |
|:---|---:|
| Labelled urgency matched | 71 / 71 |
| RED cases returned as RED | 45 / 45 |
| Missing data kept off GREEN | 78 / 78 |
| Model suggestions that lowered urgency | 0 / 284 |
| Output filter blocked unsafe sentences | 27 / 34 (misses listed) |

> *"Rules-first. Source-linked. A named human signs off. Tracked to closure. Built on Microsoft Semantic Kernel and
> aligned with Cognizant's TriZetto AI Gateway. And we tell you where it is still weak."*

---

## 3. Say it honestly

| Say | Never say |
|:---|:---|
| "Research prototype, not a clinically validated device" | "Clinically validated", "CDSCO approved" |
| "DPDP-ready by design" | "DPDP compliant" |
| "Triage support; the rules engine sets urgency; a named clinician signs off" | "AI diagnosis", "AI doctor", "detects dengue" |
| "On synthetic, author-labelled cases: 71 / 71" | "100 % accurate", "100 % sensitivity" |
| "Engines agree, checks passed — still needs your confirmation" | "Verified" for any machine-read value |
| "Runs on this laptop with Wi-Fi off" | "Works offline everywhere" or "PWA" (offline web app not built) |
| "The assistant needs the cloud; offline it is switched off" | Anything implying the assistant ran offline |
| "Hindi voice tested on synthetic clips; Odia voice not yet verified" | "Supports 22 languages" |

If asked about the limits, say: synthetic data only; no clinical validation; the output filter and redaction are
heuristics with listed misses; four open clinical questions are on the scorecard for clinical review (docs/25 §6).

---

## 4. Rehearsal and backup

- Rehearse three times with **Wi-Fi off**, timing each beat.
- Record one full run with the macOS screen recorder (`Shift-Cmd-5`) as the backup video, and keep a screenshot of
  each beat. Synthetic data only, and nothing personal on screen (close other apps, hide the dock and notifications).
- `make demo-check` again five minutes before.

## 5. Fallbacks

| Failure | What to do |
|:---|:---|
| Microphone fails | Use the **▶ Sample** clips (same backend path) |
| Voice or OCR off in `make demo-check` | Type the complaint; skip the report beat and say so |
| Local model not ready | `make llm-start`, wait about a minute; or continue from the seeded demo patient (Beats 4–6 need no model) |
| Live intake goes wrong | Open the seeded **"Fallback: the demo patient, already triaged"** and continue at Beat 4 |
| No network for the assistant | Expected offline: say so and skip it |
| Anything else | The backup video, then the screenshots |

## 6. Appendix: the Beat 4 request

```bash
# as anm_demo or mo_demo (token from POST /api/v1/auth/login); the demo backend is on 127.0.0.1:8010
curl -s -X POST http://127.0.0.1:8010/api/v1/triage/process \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scenario":"opd","age_years":28,"red_flag_screen_completed":true,
       "red_flags_present":["severe_pain"],"suspected_infection":true,
       "vitals":{"resp_rate":20,"spo2":97,"on_supplemental_oxygen":false,"pulse":96,
                 "sbp":118,"dbp":76,"temp_c":38.9,"consciousness":"A"}}'
# → RED; triggered_rules: [ATP_RED_SEVERE_PAIN (ATP_2022)]; NEWS2 2 (low); qSOFA 0
# Safety floor: "red_flag_screen_completed": false and no red_flags_present → YELLOW, needs_human_review,
#   SAFETY_FLOOR_INSUFFICIENT_DATA
```

Platelets are not an engine input: a request containing `"platelets"` is rejected (`400 VALIDATION_ERROR`). Pinned by
`backend/tests/rules/test_demo_vignette.py`, and checked on the running stack by `make demo-check`.

> **Related:** [PRD](01_PRD.md) · [Rules engine](10_Safety_Rules_Engine.md) · [Referral tracking](21_Referral_Closure_Tracking.md) ·
> [Protocol evidence](23_Protocol_Evidence.md) · [Clinical assistant](24_Clinical_Assistant.md) · [Safety scorecard](25_Safety_Evaluation.md)
