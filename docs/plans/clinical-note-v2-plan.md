# Clinical note v2: findings and plan

> 2026-11-11. Base: `main` `4f7750a`. **Status: owner approved N1–N5 as recommended on 2026-11-11. Step 1 implemented on branch `feat/per-symptom-duration` (see §10). Steps 2–7 not started.**
> Research prototype, non-diagnostic. The rules engine stays the only urgency authority. A named human signs off.

## 1. What was asked

1. After the final review, generate a summary like the supplied "Clinical Encounter Summary" example.
2. Is Llama or Qwen used to write the summary?
3. Where is the medical information stored: a database, Azure, or Claude?
4. From speech in any language, collect each symptom as a medical term with its own duration ("fever, 2 days; headache, 2 hours").
5. Would Agentic AI, A2A, IBM ACP or a hand-built MCP make the product win?

## 2. Short answers

| Question | Answer | Evidence |
|---|---|---|
| Is a summary generated? | Yes, but only on the medical officer's case screen. The health worker's final review step does not show it. | `EncounterSummaryPanel` is used only in `CaseWorkspace` |
| Does a model write it? | No. The text is template sentences built from stored facts. No Llama, Qwen, Mistral or Azure model writes the summary. | `encounter_summary.py`; owner decision D1 of 2026-11-11 |
| Is any model used at all? | Yes, for extraction only: Qwen3-4B (local, `llama-server`) reads typed text and transcripts into fields. | `AI_PROVIDER=local`; run in §3 |
| Is Azure used? | A key is set and an Azure provider exists, but extraction is set to the local model. The Semantic Kernel wiring to Azure is a connection test, not part of the patient flow. | `providers.py`, `services/kernel.py` |
| Where is data stored? | On this computer. Records are in one SQLite file, `sehat-ai/sehat.db`. Uploaded pages and images are files under `sehat-ai/data/documents/`. Nothing is stored in Azure or in Claude. | `config.py`; 8 cases and 23 document folders present today |
| What leaves the computer? | Only speech sent to Sarvam, and only with the patient's cloud-voice consent. Image description and OCR run locally. | `.env` flags, docs/12, docs/18 |
| Per-symptom duration? | Not supported. The extraction schema has one duration for the whole visit. | §3 |
| Symptoms as medical terms, any language? | Partly. The local model turned "my head is paining" and Hindi "sir dard" into "headache". It is not checked against a coded vocabulary. | §3 |

## 3. Run on this computer (2026-11-11)

The sentence from the request went through the real local model (Qwen3-4B, one pass, temperature 0) and through the fake keyword extractor.

| Input | Local model | Fake extractor |
|---|---|---|
| "I am suffering from fever since 2 days and my head is paining since 2 hours. No vomiting." | fever, headache, vomiting denied; duration **"2 hours"**; 5.9 s | fever, vomiting denied; duration "2 days" |
| "Mujhe do din se bukhar hai aur do ghante se sir dard ho raha hai." | fever, headache; duration "do ghante"; 5.0 s | nothing |
| The same sentence in Devanagari | fever, headache; **no duration**; 4.1 s | nothing |

What this shows:

- **Symptom names work**, including Hindi words, on the real model.
- **Durations are lost.** There is one duration slot, so "fever 2 days" was dropped in English and both were dropped in Devanagari.
- **The fake extractor misses "head is paining" and all Hindi.** Any demo on the fake provider will look worse than the real model.
- One sentence is not an accuracy measurement.

## 4. The example note against the product

| Example section | Today | Gap |
|---|---|---|
| 1. Patient and encounter (sex, age, facility, admission and discharge dates) | Visit type, facility, visit start, age from the triage form | Demographics and dates printed on a discharge record are not read |
| 2. Presenting complaint and history | Presenting problem, duration, symptoms, body map, interview answers | Free-text history on a discharge record is not read |
| 3. Initial observations | Measurements section | None |
| 4. Diagnosis and discharge information | Not present | Handwritten and printed free text is not read (deferred, decision D3) |
| 5. Laboratory table | Lab values with report date and check state | Shown as lines, not as a table |
| 6. Radiology | Imaging section, AI-described rows | See the warning below |
| 7. Medication and prescription | Medicines section; uncertain handwriting flagged and never used | None |
| 8. Documentation alerts (identity mismatch between documents) | A person answers "is this the patient's report?" per document | No automatic comparison of age and sex across documents |

**Two parts of the example must not be copied as written.**

- **Section 6** says the heart outline "appears within normal limits" and that there is "no obvious acute abnormality". That is a model declaring an image normal. The project's rules forbid it: non-diagnostic (Agent rule 2), and missing or unread is never "normal" (rule 5). The local image model also called a hand-drawn picture normal with 95% self-reported confidence (docs/18). The product keeps such text as labelled rows, "AI-described, not a diagnosis", and never as a statement of the note.
- **Section 4** is acceptable only as a quotation: "as written on the discharge record, not yet checked". The product may copy what a document says; it may not state a diagnosis of its own.

The example also prints a doctor's name. The summary carries no personal names today.

## 5. Agentic AI, A2A, ACP, MCP: the strict answer

No. Adding these will not win it, and adding them badly can lose it.

- **The scoring in this project's own checklist has no line for them.** `docs/02`: safety-first triage 20%, extraction and summarisation 20%, multimodal 15%, India relevance 15%, human review 15%, privacy 10%, demo 5%. Whether the organisers' real rubric matches that table was not checked.
- **A2A and ACP connect separate agents owned by different systems.** This product is one application. There is no second agent to talk to. A judge who asks "which two agents, and why a protocol?" gets no good answer.
- **An agent that decides is a safety liability here.** The strongest claim the product has is that rules set urgency and a person signs off. A model choosing its own steps weakens that claim in the 20% safety criterion.
- **System limits.** One laptop already runs three OCR engines, an image model, a speech model and a 4B language model. One local model pass takes 4 to 6 seconds. An agent loop multiplies that.
- **What is true and can be said today:** the pipeline is a fixed, audited sequence of tools with a human gate at each step. Semantic Kernel is present as a connection test and a test harness, not as an orchestrator. Do not call it "agentic" on a slide.

The one defensible addition, as a stretch only after §7 steps 1 to 4:

- **A small read-only MCP server** that exposes one visit's reviewed summary as a tool, so a payer or hospital system such as a TriZetto-style gateway could fetch it. It fits the "handoff" story, needs no new model, and cannot change urgency. It is not in the architecture document, so it needs the owner's explicit decision (N5).

What moves the score is §7: correct per-symptom extraction in Hindi and English, a note the judge can read in ten seconds, and a mismatch alert that visibly catches a wrong-patient report.

## 6. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| N1 | Per-symptom duration and severity in the extraction schema (schema change, prompt change, review screen change) | **Yes.** It is the exact gap in §3 and serves the 20% extraction criterion. |
| N2 | A model-written "readable draft" beside the template note. This reverses D1 of this morning. Options: **(a)** none; **(b)** local Qwen; **(c)** Azure OpenAI with synthetic data only | **(b), behind the existing sentence gate, shown as a second box.** Every sentence must cite facts and pass the number, tag and non-diagnostic checks, or it is dropped. The template note stays the record. Azure only if the organisers require it. |
| N3 | Read free text on discharge records and handwritten notes as quoted lines, "as written, not yet checked". This reopens D3. | **Yes, printed text first; handwriting last.** It is what the example note needs most, and it is the largest piece of work. |
| N4 | Automatic identity check across documents (age and sex printed on each), raised as an alert. Never auto-links or auto-rejects. | **Yes.** Small, visible, and a strong demo moment. |
| N5 | Read-only MCP server for the reviewed summary | **Only if steps 1 to 4 are finished and verified.** |

Also needed from the owner: the hackathon date and the organisers' actual judging sheet. Without them the order below is a judgement, not a fact.

## 7. Plan (tests first in every step; nothing starts before N1–N5 are answered)

### Step 1. Per-symptom structure (N1)

- Schema v2: each symptom gets optional `duration`, `onset` and `severity`, each with its own verbatim quote. The single visit-level duration stays for older rows.
- Prompt v2 with two worked examples (English, Hindi). `PROMPT_VERSION` bump.
- Grounding: a symptom's duration must be quoted from the same segment as the symptom.
- Summary: "Fever, 2 days. Headache, 2 hours." from per-symptom facts; the current single-symptom linking rule becomes the fallback.
- Review screen: duration shown and correctable per symptom.
- Fake provider: learn "X since N unit" per clause, so tests and the corpus cover it.
- Evaluation: new cases for two symptoms with two durations in English, romanised Hindi and Devanagari. Real-model run recorded separately from the mock run.

### Step 2. Summary at the final review (asked first in the request)

- Build the summary when triage is submitted, and show the note on the health worker's final step, read-only, with the same labels.
- The medical officer's screen is unchanged.

### Step 3. Identity alert across documents (N4)

- Read printed age and sex from each document header (already in the OCR text; no new engine).
- Compare with the triage form and between documents. A difference becomes a "Documentation alert" in the note and under "Needs attention".
- It never links, unlinks or hides a document by itself. The person's answer still decides.

### Step 4. Note layout to match the example

- Numbered sections, a lab table, an alerts section at the end, "Draft · human verification required" in the header.
- Print layout updated; the PDF check extended.

### Step 5. Quoted document text (N3)

- Printed discharge-record lines first, each shown as "as written on the document, not yet checked", each confirmable by a person.
- Handwritten lines only where both engines agree; otherwise "not legible, see the image".
- A quoted diagnosis is allowed only inside this section and only as a quotation. The guard gets one narrow, tested exception for quoted document text.

### Step 6. Readable draft (N2)

- One local model call writes prose from the fact list only. Each sentence goes through the existing gate. A dropped sentence is counted and shown.
- Labelled "Model-written draft from the facts below. The checked note is the record."
- If the model server is not running, the box is absent. There is never a fake fallback.

### Step 7. Stretch: read-only MCP server (N5)

### Verification for every step

- Full backend and frontend suites, the skip gate, the corpus rerun.
- `e2e_summary_note_browser_check.py` extended, in fake and real modes.
- A check in Chrome on the running app.

## 8. System limits to plan around

- **The local language model must be started by hand** (`scripts/start_local_llm.py`). It was not running today, so AI extraction on the running app answers "not available".
- **Three passes per extraction** at 4 to 6 seconds each on this laptop.
- **Sessions end at every backend restart**, because no signing key is set in the settings file. Set one before a demo.
- **English speech needs the cloud engine.** The local speech model has Hindi and Odia only.
- **Romanised Hindi typed into an interview answer is refused** by the identifier check.

## 9. Not verified in this plan

- The organisers' real judging criteria.
- Any accuracy figure for the local model. §3 is three sentences.
- The effort for each step. No estimate is given because none was measured.

## 10. Step 1 record: per-symptom term and duration (2026-11-12)

**What changed**

- Extraction schema `.2`: a symptom may carry `term` (closed list of 30 everyday terms), `duration` (the words said), `duration_amount` and `duration_unit`. Older rows still validate.
- Prompt `.2`: one item per symptom, the patient's words as the name, worked examples in English and Hindi, and examples for denied symptoms.
- Grounding: a symptom's duration is kept only when its words are in that symptom's own quote; its number only when the quote contains that number (digits or number words, English or Devanagari). Otherwise the duration, or just its number, is dropped and listed, and the symptom is kept. A denied symptom never carries a duration.
- Voting: a symptom is keyed by its term, so passes that word it differently vote on the same item. Passes that disagree on a duration are a disagreement.
- Summary schema `.5`: "Fever for 2 days. Headache for 2 hours." Each symptom's duration is compared only with itself. A visit-level duration that repeats a symptom's own is written once. The interview's duration answer conflicts only when it matches none of the recorded durations.
- Review: a reviewer can correct one symptom's duration (number and unit, both or neither). The Questions step shows "headache (said: head is paining), 2 hours".
- Not built from the plan's step 1: per-symptom **severity** and **onset**. Only term and duration were added.

**Checks**

| Check | Result |
|---|---|
| Backend suite | 1619 passed, 14 skipped (opt-in live tests), 5 expected failures; skip gate ok (minimum 1568, 25 required modules) |
| Frontend | 142 tests passed; TypeScript clean; ESLint 0 errors |
| Corpus (mock) | 47 cases, all metrics pass; 6 new symptom cases |
| Browser check, fake mode | 33 / 33, including the review screen and the note |
| Real local model (Qwen3-4B, one pass, 9 synthetic sentences, after grounding) | terms 15 / 15; exact durations 11 / 11; number never guessed when unverifiable 4 / 4; valid replies 9 / 9; **denied symptoms 1 / 4**; no extra present term 8 / 9; 4.9 s per sentence |

**What the real run showed**

- English and Devanagari durations came out exact, including "एक हफ्ते" as 1 week.
- Romanised Hindi ("do din") keeps the words but not the number: the number parser does not read romanised number words, so nothing is guessed.
- **Denied symptoms are the weak point.** "No vomiting" was found. "I do not have fever" and "bukhar nahi hai" were left out by the model. A left-out denial is not shown as present, but the denial is lost.
- **A Devanagari denial is shown as present with "negation unclear".** The model marked "उल्टी नहीं है" as denied, but the negation rule accepts only Latin-script cue words, so it falls back to "present, check this". It stays out of the written text and is listed for review. This is the existing safe-side rule; it makes every Hindi-script denial need a person.
- The first version of the new prompt made the model drop all denials. That was caught by this run and fixed by adding denial examples; unit tests could not have caught it.

**Label changes in the corpus** (each noted in the file): `duration_words` now links the duration to fever; `contradictory_durations` now conflicts on fever's own duration.

**Open for a decision**

- Devanagari negation cues in the grounding rule (a clinical-safety rule; not changed here).
- Whether denials should get a dedicated second pass, since one pass of a 4B model misses some.

### Through the running app, real engines (2026-11-12, scratch database, synthetic input)

| Path | Input | Result |
|---|---|---|
| Typed English, Questions step in Chrome, Qwen3-4B, 3 passes | "I am suffering from fever since 2 days and my head is paining since 2 hours. No vomiting." | fever, 2 days and headache (said: head is paining), 2 hours, each agreed 3 of 3. The note reads "Fever for 2 days", "Headache for 2 hours". **"No vomiting" was not extracted.** |
| Spoken Hindi (repository clip), local speech model, IndicTrans2 translation, Qwen | "मुझे तीन दिन से बुखार है। तापमान 102 डिग्री है।" | Note: "Fever for 3 days [not yet checked]". The temperature was held back because no unit was said. |
| Spoken Hindi (synthetic voice made with the system text-to-speech), same engines | "मुझे दो दिन से बुखार है और दो घंटे से सिर में दर्द हो रहा है। उल्टी नहीं है।" | fever 2 days, headache 2 hours, vomiting denied, each agreed 3 of 3; all marked machine-translated and not yet checked. |
| Typed Hindi | the same sentence, typed | **Refused: the app accepts English text only.** Hindi reaches the model only as speech, through translation. |

Also seen: the model left the chief complaint empty in each run, so the note lists "Chief complaint" as missing although symptoms were found.
