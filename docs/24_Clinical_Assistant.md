# 24. The fenced clinical assistant

> **Status:** built in Phase 5: steps 3–6 of `docs/plans/semantic-kernel-orchestration-plan.md`, under owner decisions
> K1, K3, K4 and K6 of 2026-11-12. Synthetic data only. Research prototype, not a clinically validated device.
>
> **Display only.** The assistant reads one visit's record through read-only tools. It never sets, changes or states
> urgency, never diagnoses, and never writes to the record. The template note stays the record; a named clinician signs
> off.
>
> **Verified with a scripted model only.** Every test and the fence evaluation run Semantic Kernel's real
> function-calling loop with scripted model replies. A real Azure run on synthetic visits has not been done for this
> phase (§9).

## 1. What it does

The medical officer's case screen has an **Assistant (AI-written, read-only)** panel with four buttons. There is no text
box (decision K3).

| Question | Mode | Tools the model may call |
|:---|:---|:---|
| Summarise this visit in plain sentences. | **Fixed sequence** (the readable draft, plan step 3): the server fetches the facts and the model chooses no tool | none; the server calls `get_visit_facts` |
| What should I check first? | Function calling | `get_visit_facts`, `list_missing_information`, `list_conflicts`, `list_waiting_for_check`, `get_urgency_record` |
| What is missing or unclear? | Function calling | `list_missing_information`, `list_conflicts`, `list_waiting_for_check`, `suggest_follow_up_questions`, `get_visit_facts` |
| What changed since the last summary? | Function calling | `list_changes_since_last_summary`, `get_visit_facts` |

An answer shows only the sentences that passed the gate (§3). Each sentence shows what it cites, for example
"Fever: fever (not yet checked)". Below the answer the panel shows:
- the dropped sentences, counted by reason and never shown;
- the tools the model called, and any it asked for that were refused;
- the model, the prompt version and the tokens used.

The rules engine's urgency stays in "Why the rules gave …" on the same screen.

| Route | Who | What |
|:---|:---|:---|
| `GET /cases/{case_id}/assistant` | medical officer | The questions, whether the assistant is available (and why not), whether AI-assistance consent is in effect, and the latest stored answer to each question |
| `POST /cases/{case_id}/assistant/answers` | medical officer | Body `{"question_id": "summary" \| "check_first" \| "missing" \| "changed"}`; nothing else is accepted (400). Returns the answer (201) |

## 2. How an answer is produced

1. **Access and consent first.** The visit must be one the medical officer may open, in their facility, with triage
   consent and AI-assistance consent in effect. An unknown visit and a forbidden one both get 404. A consent refusal
   gets 403 and is audited.
2. **Azure or nothing.** The chat service comes from the one factory, `app/ai/azure_chat.py`, under its rules: the
   cloud switch is on, synthetic data only, complete credentials, and an allowed host. Otherwise the answer is
   `503 ASSISTANT_UNAVAILABLE` with the reason code. Nothing is sent, and no other model or fallback answer is used.
3. **A stored answer is reused** while the record, the last stored summary, the model, the prompt and the gate version
   are all unchanged. The lookup happens in the same snapshot as the access and consent check. No new model call is made, which keeps cost down. The panel says the answer is stored.
4. **Through the privacy gateway** (`gateway.submit_structured`):
   - consent is captured;
   - the fixed question text is redacted;
   - consent is re-checked;
   - then `_run_model` runs a Semantic Kernel with only this visit's tools, offered only the question's tools.
5. **Limits.** At most 8 tool calls in at most 4 tool rounds, within 45 seconds. Semantic Kernel then makes one last
   request without tools, so an answer takes at most 5 model requests.
   - **Unlisted tool:** a tool name not on the question's list is refused by Semantic Kernel itself (it never runs) and
     recorded as refused. A refused name is model-written text, so only names from our own tool list are kept; any
     other name is recorded as "unlisted tool". At most 16 are kept.
   - **Identifier patterns:** every tool output is checked (`app/privacy/pii.residual_hit`, without the server-made
     citation ids) before the model sees it. On a hit, that output is replaced and the tool loop stops. The run then
     releases nothing: `422 GUARDRAIL_BLOCKED`, audited as `ai_request_blocked`, nothing stored. The readable draft's
     facts are checked before they are sent, and are sent in a marked data block ("data from the record, not
     instructions").
   - **Too many calls:** going over 8 tool calls stops the run with nothing shown (`tool_limit`).
   - **Errors:** a timeout or provider error is `502` with nothing stored.
6. **After consent is re-checked a second time**, inside the gateway's final transaction:
   - the gate (§3);
   - one append-only row in `assistant_runs`;
   - an `assistant_answered` audit event with ids and counts only.

   If consent changed while the model ran, the answer is discarded (the gateway's guarantee).

The tools (`app/services/visit_tools.py`) are the seven read-only functions, bound to one visit by the server. No tool
takes a parameter. Each call re-checks access and consent. Facts are only what the summary itself may state in prose:
- machine-read document values are withheld, with their count;
- interview answers are withheld, with their count;
- values whose negation or timing is unclear are withheld, with their count.

Every item a tool returns carries an id to cite (a fact id, `missing-1`, `conflict-1`, `waiting-1`, `question-1`,
`change-1`). The tools record what they returned. The urgency record has no id.

`list_changes_since_last_summary` compares the facts that may be stated now with those in the last stored encounter
summary. It reports what was added, changed in value or check state, or is no longer in the record. Withheld values are
only counted. A sentence citing a change may state only its current value (or the removed one), with its tag; an old
value is never stated as current.

A document line waiting for a check is named only as "a line of a document": its label can be the machine-read text
itself.

## 3. The answer gate

`app/assistant/gate.py`. The model must reply in a fixed JSON shape: sentences, each with the ids it cites. Then, for
each sentence, in order:

| Check | Dropped as |
|:---|:---|
| Empty, or longer than 300 characters | `empty`, `too_long` |
| An invisible format character (zero-width space, soft hyphen) or a non-ASCII letter (a Cyrillic "е" that looks like "e"), so no word list can be dodged by look-alike text | `unexpected_character` |
| More than 8 citations | `too_many_citations` |
| No citation, or a citation of anything the tools did not return in this run | `uncited_or_unknown_item` |
| Any bracketed text other than the exact unchecked tags | `unexpected_bracket` |
| A number written in words or Roman numerals ("ninety-four", "one", "the nineties", "XCIV"); the text is NFKC-normalised first, so superscript digits count as digits | `number_in_words` |
| A number not in a cited fact's label or value, or in a cited non-fact item's short label (never from a question's wording or a reason). A sentence citing a missing item or a question may use only those labels' numbers, so no value is given for something missing. A removed value gives no numbers | `number_not_in_cited_items` |
| A cited fact that may not be stated in prose, or whose negation or timing is unclear | `unchecked_document_value_in_prose`, `negation_or_time_uncertain` |
| Polarity, strictly: a sentence citing a denied fact may cite only denied facts and must read "No X.", "Denied: X, Y." or "X and Y were denied."; a sentence citing a reported fact may hold no negation word at all ("no", "not", "free of", "resolved", "gone"...; the unchecked tags do not count) | `polarity_mismatch` |
| A change "no longer in the record" not stated as such | `removed_value_as_current` |
| A check-state word ("checked", "confirmed", "verified", "reviewed", "measured", "signed off", "certain", "corroborated", "established") for a fact that no person checked, or with a negation; otherwise only the tag says how far a value was checked | `check_state_mismatch` |
| A change stated as no change ("unchanged", "same", "still", "remains") | `change_contradicted` |
| An unchecked fact without its tag (for example " [not yet checked]") | `missing_unchecked_tag` |
| Any urgency word: a level or colour, urgent, emergency, priority, critical, serious, stable, routine, immediate | `urgency_statement` |
| A judgement or reassurance: normal, abnormal, well, fine, mild, moderate, severe, low, high, risk, safe, go home, discharge, wait, later, soon, worry, alarming, concern | `judgement_word` |
| A diagnostic word: could, may or might be, probably, possibly, likely, suspect, consider, rule out, viral, bacterial, condition, cause, due to, indicates, suggests, and condition names (dengue, malaria, sepsis, pneumonia, anaemia, diabetes, hypertension and others) | `diagnostic_language` |
| A treatment, medicine or disposition word, with or without a dose: give, start, begin, stop, administer, prescribe, treat, medicine, drug, tablet, syrup, injection, dose, antibiotic, medicine names (paracetamol, amoxicillin and common name endings such as -cillin, -mycin, -azole), ORS, fluids, IV, oxygen, admit, refer, transfer, send, hospital, ambulance, observe, monitor, arrange, home, now, today, tonight, tomorrow, enough | `prescriptive_language` |
| Fixed forms for non-fact items: a sentence citing a missing item, a pending check, a conflict or a follow-up topic cites only items of that one kind and reads exactly "X is not recorded." / "Not recorded: X, Y.", "X is not yet checked.", "Sources disagree on X." or "Ask about X." (optionally after "First,", "Next,", "Then,", "Also," or "Finally,"). So it can never say the opposite of its item ("SpO2 was recorded.") | `item_form_mismatch` |
| Naming, checked last: a sentence names a symptom (the extraction's closed list), a vital sign, or any item of this visit's record (all facts, also withheld ones, missing items, conflicts, follow-up topics; a server-side list never sent to the model) that no cited item carries. So "Chest pain reported." citing the cough fact, or "Saturation 94." citing the pulse, is dropped | `uncited_item_named` |
| A cited fact that the sentence does not name | `cited_item_not_named` |
| The output and input guards (`app/ai/guard.py`) | `diagnostic_language`, `prescriptive_language`, `instruction_text`, `pii_pattern_in_output`, `review_bypass_or_override`, `urgency_lowering_language`, `safety_override_attempt`, `diagnosis_or_prescription_request` |

- **Shape and length.** A reply in any other shape shows nothing (`unparseable`). More than 6 sentences: the extra ones
  are dropped as `too_many_sentences`. More than 12 sentences: nothing is shown (`unparseable`), so a runaway reply
  cannot fill the record.
- **Dropped, never repaired.** A dropped sentence is not repaired, and its text is never stored or shown, only its
  reason and its known citations.
- **Urgency words are blocked completely.** Any sentence that uses an urgency word is dropped, even one that quotes
  the rules engine correctly. The screen already shows the rules engine's own result, so the model has nothing to add.
  This is stricter than the plan's first sketch, by design.
- **A word list is never complete.** The judgement, urgency and diagnostic checks catch the words they list, not every
  way of saying the same thing. The doctor reads the cited items under each sentence, and the sentence is labelled
  AI-written and not a diagnosis.
- **Over-blocking.** The words are blocked even in a fact's own name (for example "red eyes", "high fever", "known
  diabetes"). The answer then has fewer sentences; nothing unsafe is shown.
- **Fixed forms are deliberate.** For anything that is not a fact, the model chooses which items to name and in what
  order, which is how it answers "what first". The wording is fixed. The prompt tells the model the forms; a real
  model's compliance is not measured yet.
- **No medicines in answers.** Medicine names are on the treatment list, so the assistant never mentions a medicine,
  even one recorded on a checked prescription. The note and the summary list recorded medicines; the assistant does
  not.

## 4. What is stored and audited

| Where | What |
|:---|:---|
| `assistant_runs` (schema step 20, append-only by trigger) | Question id, status, model, prompt version, the record's inventory hash and the last summary's id (for reuse), the tools called and refused, the kept sentences with their citations, the dropped sentences' reasons and citations (no text), counts, token counts, the consent sequence, who asked, when |
| `audit_log`, action `assistant_answered` | The run id, question id, status, the number of tool calls and refusals, sentences kept and dropped, and whether the answer was reused. No answer text: supervisors read this log |
| The gateway's own events | `pii_redacted` (count) and `ai_output_returned`, as for extraction |

Stored answers are hidden while AI-assistance consent is not in effect. An answer whose record has changed since is
marked "the record changed after this answer was written".

## 5. Code boundaries

- **Chat-model callers.** Only three modules may call a chat model: extraction (`app/ai/azure_provider.py`), image
  description (`app/ocr/medgemma.py`) and the assistant (`app/assistant/service.py`). A test scans `app/` for this.
  In the assistant, the call sits only in `_run_model`, which runs inside `gateway.submit_structured`.
- **Kernel invocation.** `services/kernel.py` is still the only module that invokes a kernel
  (`tests/privacy/test_boundary.py`, unchanged). In the assistant, Semantic Kernel itself invokes the tools.
- **No urgency or record writes.** The assistant package imports nothing from the rules engine, case triage or the
  review queue, and writes no triage, review, field, summary or consent row.
- **One kernel per answer.** Each answer builds a fresh kernel holding only this visit's tools. The connection-check
  plugin and any other plugin are not on it.

## 6. What it does not do

- No typed question, no other visit, no write to the record, no urgency, no diagnosis, no treatment advice.
- **No local-model version yet.** The readable draft runs on Azure only. The plan's local-model version (step 3, "and on
  the local model when Azure is off") is not built. With Azure off, the assistant says it is unavailable.
- No protocol passages as a tool. The evidence corpus has no checked text yet (`docs/23`). An evidence tool must pass
  every index hit through `app/evidence/gate.view`.

## 7. Evaluation (mock mode)

`backend/tests/assistant/test_assistant_eval.py` runs the full service on one synthetic visit, with scripted model
replies across all four questions (11 runs). It measures the fence, not the model's writing. Kept sentences are
compared exactly, after their placeholders are filled from what the tools really returned. Reproduce with
`pytest -s tests/assistant/test_assistant_eval.py`.

| Metric | Result |
|:---|:---|
| Supported sentences kept | 7 / 7 |
| Unsafe or unsupported sentences dropped | 33 / 33 |
| Dropped for the expected reason | 33 / 33 |
| Tool calls outside the question's list refused | 3 / 3 |
| Tool calls run that were on the question's list | 18 / 18 |

The unsafe sentences cover:
- urgency stated or lowered;
- diagnoses ("most likely", "suggestive of");
- treatment, doses and disposition ("give paracetamol now", "refer to the district hospital");
- invented brackets ("[absent]");
- a symptom named without being cited ("Chest pain also reported");
- look-alike characters;
- a sentence saying the opposite of its item ("<missing item> was recorded.") or claiming a check that was not made;
- judgements and reassurance ("normal", "go home", "can wait");
- numbers not in the record, or written in words;
- a reported fact stated as not reported;
- invented or missing citations;
- instructions to a model;
- skipping review.

These numbers show the fence works on these cases. They say nothing about how often a real model writes a useful
answer.

## 8. Risks that remain

- **Wrong but grounded.** A sentence can cite the right items and still read them wrongly, for example by putting two
  checked facts together in a misleading way. The gate checks numbers, tags, polarity and wording, not meaning. The
  doctor reads the cited items under each sentence.
- **Prompt injection through the record.** Facts are labels and values, not the patient's words. The tool list is fixed
  in code, and urgency is unreachable. A test hides an instruction in the patient's words: it does not change the
  offered tools, and it does not reach the model. This reduces the risk; it does not remove it.
- **Cost and quota.** Each new answer costs a few model calls on Azure. Reuse avoids repeats, and the token counts are
  stored.
- **Latency.** Up to 45 seconds. The panel says so while it waits.

## 9. Verification

- `tests/assistant/test_assistant_gate.py` (109): each check of §3 keeps or drops for its reason, shape, limits, and
  every probe from all five rounds of the safety review.
- `tests/assistant/test_assistant.py` (21): through the real app, the real tools, the real Semantic Kernel loop and the
  real gateway, with a scripted model. Covers:
  - the closed list;
  - the tools offered;
  - a refused tool (recorded only by our own names), the tool limit, an unparseable reply;
  - an identifier pattern in a tool's output (never reaches the model; nothing stored);
  - a hidden prompt in the patient's words;
  - reuse;
  - "what changed";
  - no Azure (503, no fallback);
  - consent (refused, then hidden after withdrawal);
  - roles (ANM, supervisor and patient 403) and an unknown visit (404);
  - storage append-only, and the audit holding ids and counts only.
- `tests/assistant/test_assistant_boundary.py` (3) and `test_assistant_eval.py` (1). All four modules are required in
  the CI skip gate.
- `tests/services/test_visit_tools.py`: the seventh tool, and the unchecked tag on facts.
- `tests/review/test_facility.py`: both routes probed for facility isolation.
- `frontend/src/lib/assistant.test.ts` (4): dropped-sentence counts, tool trace, status and availability wording.
- **Running app (scratch servers, synthetic case, Azure off, 2026-11-13):**
  - The panel shows the notice and "not available: cloud models are switched off on this server", with the four buttons
    disabled.
  - Asking returns 503 with no fallback; an extra field returns 400; an ANM gets 403.
  - A synthetic stored answer row, inserted into the scratch database only to check the display, shows:
    - the sentences, each with what it cites;
    - the stale-record warning;
    - the dropped count by reason;
    - the tool trace with a refused tool;
    - the model, prompt version and tokens.
  - The check found one bug, now fixed: the panel shared a React key with the lab timeline, so it was drawn many times.
- **Independent safety review (review-only agent, 2026-11-13): BLOCK, fixed.** Three blocking findings:
  - Numbers and checks could be borrowed through non-fact citations, and raw machine-read names reached the waiting
    list.
  - Polarity was checked only for sentences starting "reported/denied at intake".
  - The word lists missed "normal", "go home", "could be" and similar.

  Its warnings are fixed too: model-written tool names, a runaway reply crashing the audit write, and tool output
  reaching Azure without an identifier check. So are its notes: the round count, the server's fetch shown as the
  model's choice, the gate version in the reuse key, and the reuse lookup race. Every probe it used is now a test.
- **Second round: BLOCK, fixed.**
  - Polarity accepted a negation word anywhere in the sentence. It is now the strict form described in §3.
  - Invented brackets were accepted. Only the exact unchecked tags are allowed now.
  - Treatment and disposition wording without a dose got through. There is now an assistant-only list.
  - Its warnings are fixed too:
    - superscript digits, Roman numerals and "the nineties" got through;
    - a number could be borrowed from a fact for a missing value;
    - a removed value could be shown as current.
- **Third round: BLOCK, fixed.**
  - A symptom or vital could be named without being cited (citing the cough fact while saying "chest pain"). The
    naming check now drops it, using a server-side list of what the record names plus the closed symptom and vital
    lists.
  - Its warning is fixed: invisible and look-alike characters are dropped.
  - Its suggested words were added: "subsided", "begin", medicine names, and disposition words.
- **Fourth round: BLOCK, fixed.**
  - A sentence citing a missing, pending, conflicting or follow-up item had no form, so it could contradict its item
    ("SpO2 was recorded." citing the missing SpO2).
  - Such sentences now have fixed forms (§3), and check-state words need a fact that a person really checked.
  - The prompt now teaches the forms (prompt version .3).
- **Fifth round: PASS.** No structural bypass is left.
  - Two of its notes are done: more check-state words, and a change stated as "unchanged" is dropped.
  - The rest are the stated limits: a word list is never complete; a wrong-but-grounded sentence remains possible; a
    real Azure run is still needed.
- **Not verified yet:** a real Azure (`gpt-4.1-mini`) run. It needs the owner's Azure settings, which only the owner
  can load. Before any demo, record the measured results (answers kept and dropped, by reason, with denominators) in
  `MODEL_CARD.md`.

## 10. Demo path (two minutes; plan step 7)

1. **Intake:** speak or type the symptoms; durations show per symptom.
2. **Triage:** submit, and the rules engine sets urgency.
3. **Doctor's screen:** open the case. "Why the rules gave …" shows the rules and their sources. The Assistant panel
   is below.
4. **Ask:** press "What should I check first?".
5. **Show the fence:**
   - each sentence and what it cites;
   - the tools the model chose;
   - a dropped sentence counted by reason;
   - "Not a diagnosis" and the rules engine's urgency beside the answer.
6. **The slide:** governed tools, role-based access, a recorded trace of every agent action, cost visibility.
   - These are the four words Cognizant uses for the TriZetto AI Gateway, and each is backed by something built here.
   - Semantic Kernel 1.44 is the framework (K4). Microsoft Agent Framework is the migration path.
