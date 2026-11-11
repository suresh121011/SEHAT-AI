# Scripted voice interviewer: plan

> Branch `feat/scripted-voice-interviewer` (from `main` `fc49e1d`). 2026-11-10. **Status: plan approved; owner decisions I1–I4 recorded on 2026-11-10. Implementation not started.**
> This replaces Niranjan's Gemini Live duplex follow-up (commit `b1e5656`), removed under owner decision D1 (commit `bea77f0`, merged in PR #14). Research prototype, non-diagnostic: the deterministic rules engine stays the only urgency authority.

## 1. Goal

On step 6, **Questions** (`/intake/follow-up`), the health worker can run a **voice interview**:
1. The app reads out one **fixed, pre-written question** at a time.
2. The patient answers aloud.
3. The answer is transcribed by the **existing, consent-checked voice pipeline**.
4. The health worker sees the answer, confirms or corrects it, and moves on.

Nothing is generated or chosen by a model, and nothing is saved as fact without a person confirming it.

## 2. Why this is safe where Gemini Live was not

| Gemini Live problem (D1) | Scripted interviewer |
|---|---|
| The model decided what to ask and said it aloud, with no output check | Questions are a **fixed list** from `app/ai/question_bank.py`, chosen by the rules engine's **missing-fields** list. The spoken text is exactly that list's text: no model output is spoken. |
| Audio went to Google under a consent that names only Sarvam | Audio goes through the **existing** `/voice/transcriptions` path: the local engine, or Sarvam only with `voice_cloud` consent, checked before the body is read and again after VAD. |
| The token was in the WebSocket URL; WebSocket hijacking was possible | **No WebSocket and no new credential path.** Ordinary same-origin POSTs with the httpOnly session. |
| Captured fields were model-written and dead-end | Each answer is the **transcript span** with its source and a human confirmation. Vital values reuse the existing **read-back → prefill** path, so they reach the triage form only when a person enters them. |
| No review | Every answer is append-only, audited (ids and enums only), and shown to the MO with its source and status. |

## 3. Scope

**In scope (v1):**
- **Questions:** the existing deterministic English question bank, driven by the rules engine's missing-fields list (danger signs first; at most 5 patient questions and, counted apart, at most 5 health-worker tasks).
- **Two kinds of entry in the bank.**
  - Patient questions are spoken: main problem, duration, severity, danger signs, LMP, medicines, adherence.
  - Health-worker tasks are shown, never spoken: "Please measure and record SpO2 / BP / pulse / temperature / breathing rate".

  Each script entry carries `audience: patient | worker`. Only patient entries get Listen and Record. Worker tasks keep the existing typed entry, or the ANM may dictate the reading, which goes through the same candidate read-back.
- **Speaking the question:**
  - the **on-device browser voice** by default (local voices only, as fixed in V1);
  - optionally **Sarvam TTS** of the *question text only*, with `voice_cloud` consent;
  - always shown as text too.
- **Answering:** recorded with the existing recorder and transcribed with the existing engines and consent gates. The ANM then chooses:
  - **choice questions** (danger signs, red-flag screen, "not sure"): the ANM picks the option, with the transcript shown beside it. Keyword *suggestions* are highlighted but never auto-selected;
  - **number questions** (SpO2, BP, pulse, temperature, breathing rate): the existing candidate extraction and read-back. Only a confirmed value reaches the prefill;
  - **free text** (main problem, medicines): the transcript span, which the ANM may edit. It is stored as "as recorded, checked by the health worker".
- **Who answered:** the ANM picks the speaker (patient, caregiver or ANM). It is never assumed.
- **Storage:** new append-only `interview_answer_events`. The latest event per question counts. Nothing is ever deleted.
- **MO view:** a read-only "Interview answers" table in case review and the encounter summary. These answers go in a **table only, never in summary prose** (v1).

**Out of scope (v1):**
- Hindi and Odia spoken questions: Niranjan's drafts are `draft_unreviewed` (docs/13).
- Adaptive question order: Phase A's hypothetical rules re-runs.
- Condition question packs.
- Auto-filling the triage form, or marking the red-flag screen complete.
- Streaming or duplex audio.

## 4. Safety rules (each one becomes a test)

1. **No urgency writes.** The interview module never imports the run writer or the review queue, and never writes `triage_runs`; the PR #14 urgency-authority test is extended to it.
2. **Spoken text is fixed.** The TTS endpoint speaks only the server's stored question text for a **patient** question id. It never speaks client text and never speaks a health-worker task.
3. **Consent.** Recording follows the existing T1/T2 checks. Sarvam TTS needs `voice_cloud`. A withdrawal stops the interview and hides its answers, as it does for transcripts.
4. **The red-flag screen is never completed by voice.** A "one or more present" answer prompts the ANM to tick the triage form and tell the MO; it records nothing as a screen.
5. **A "not sure" or unanswered question is never a negative,** and is listed as still missing.
6. **Nothing auto-selected.** A keyword match is a suggestion; only an explicit ANM tap records an answer.
7. **Who may act:**
   - Answers are recorded only by the visit's ANM or handler (`write`).
   - The MO reads.
   - A supervisor sees metadata only (D-B).
   - A bound patient (OIDC) gets read-only access, like other case data.
   - Facility scope applies.
8. **Privacy.** The audit log holds ids, question keys and enums only, never answer text. Answer text is never logged.

## 5. Design

**Backend: new `app/interview/` package (does not collide with the WIP `app/patient/`):**
- `script.py`: the scripted question list. A pure function of the scenario and the latest triage input or missing fields, reusing `question_bank.questions_for` and `rules.required_fields.missing`. Each entry has a stable question id, the key, the text, the kind (`choice`, `number` or `text`) and the options.
- `service.py`:
  - start or continue an interview;
  - record an answer: the question id, the answer kind and value, the source (`typed`, or `transcript` with transcription id and character span), the speaker, `confirm: true`;
  - list answers.

  Each change runs in one transaction with consent checks and an audit event.
- **Migration 18:** `interview_answer_events`, append-only. It references `cases` and `voice_transcriptions`, records `consent_seq`, and carries CHECKs on kind and speaker.
- **Routes:**
  - `GET /cases/{id}/interview`: the script, and the answers so far.
  - `POST /cases/{id}/interview/answers`.
  - `POST /cases/{id}/interview/questions/{qid}/tts`: Sarvam, question text only, `voice_cloud` consent.

  All three join the PR #14 facility probe list.
- **Encounter summary:** an `interview` table section, never prose, and the summary goes stale when answers change. The schema version bumps to `.4`.

**Frontend (`/intake/follow-up`):**
- An "Interview by voice" mode beside the existing typed questions. Per question it shows:
  1. the question text and a **Listen** button (device voice, or Sarvam with consent and clearly labelled);
  2. **Record answer** (the existing recorder component);
  3. the transcript;
  4. the answer control: choice buttons with suggestions highlighted, the number read-back, or an editable text box;
  5. who answered;
  6. **Confirm and next**.
- At the end, a review list of the answers and the questions still missing.
- When the device has no local voice, it shows "Read the question aloud" (the existing behaviour).

**Reused, not rebuilt:**
- the recorder, transcription, VAD, engines and consent gates;
- `voice/extract.py` candidates, read-back and prefill;
- `useMatchingVoice` with on-device-only voices;
- `question_bank`;
- `field_aliases.canonical_field`, to map spoken field names (the first real use of Niranjan's retained code).

## 6. Tests (written before the code)

- **Script:** it is deterministic for a given input; danger signs come first; at most 5 patient questions and at most 5 health-worker tasks; it never includes text that isn't in the bank.
- **Answers:**
  - only the ANM or handler may record;
  - an answer needs triage consent and `confirm: true`;
  - a transcript source must belong to this case;
  - the answers are append-only, and the latest one counts.
- **Never:** no `triage_runs` write, red-flag screen not completed, "not sure" not treated as a negative, no auto-selection.
- **TTS:** speaks only stored question text, needs `voice_cloud`, and is refused after withdrawal.
- **Isolation and roles:** facility probes for the 3 new routes; supervisor sees metadata only; patient read-only.
- **Audit and logs:** no answer text reaches the audit log or the logs.
- **Summary:** the interview table appears, the prose is unchanged, and the summary goes stale on a new answer.
- **Frontend:** the script and answer state machine as a pure helper with `node --test`; tsc and ESLint clean.
- **CI:** the new modules join the skip gate's required list, and the minimum test count is raised.

## 7. Milestones (each one a small PR)

| # | Milestone | Size |
|---|---|---|
| V1 | Script and migration 18 and answer service, with tests (backend only) | M |
| V2 | Routes and question TTS and facility probes and audit | S |
| V3 | Follow-up page voice mode (frontend) | M |
| V4 | MO view and encounter summary table | S |
| V5 | Docs (docs/12, docs/13 entry for future hi/or wording, docs/19) and a browser checklist | S |

## 8. Decisions for the owner

| # | Decision | Options | Recommendation |
|---|---|---|---|
| I1 | Languages in v1 | English only / English plus Niranjan's hi and or drafts shown as text only, labelled "draft, not reviewed" | **English only.** The hi and or drafts wait for native-speaker and clinical review (docs/13). |
| I2 | How questions are spoken | Device voice only / device voice, plus optional Sarvam TTS with `voice_cloud` consent | **Both**: device by default, Sarvam optional and clearly labelled |
| I3 | Question source | Fixed missing-fields bank (at most 5) / a longer fixed script per scenario | **Missing-fields bank**: already written, deterministic and tested |
| I4 | Where answers appear for the MO | Case review table only / also an encounter summary table (never prose) | **Both**, table only |

**Owner decisions (2026-11-10):**
- **I1:** English only.
- **I2:** device voice by default, with optional Sarvam TTS under `voice_cloud` consent.
- **I3:** the missing-fields question bank (at most 5).
  - **Amended 2026-11-11 (owner, after a browser check):** the limit of 5 is counted separately for patient questions and for health-worker tasks. Before, a visit with no vitals used four of the five places for measurement tasks and offered the patient one question. The order (danger signs first) and the question text are unchanged. Script version `.2`.
- **I4:** case review and an encounter-summary table, never prose.
