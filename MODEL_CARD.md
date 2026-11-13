# SEHAT AI: model card

SEHAT AI is a **system**, not one model: a deterministic rules engine decides urgency, and several pinned models and
engines extract, read or describe inputs for human review. This card covers each component that uses a model, plus the
deterministic components a reader might mistake for one. It is a **research prototype, not a clinically validated
device**; every result below is on synthetic data written by the project team.

## Intended use

| | |
|:---|:---|
| **Users** | Auxiliary nurse midwives (ANMs) and medical officers at Indian primary health centres; the demo is set in Odisha |
| **Task** | Structured, source-linked intake (speech, typed text, lab reports, prescriptions, images) and a protocol-based urgency level for a clinician to review and sign off |
| **Decision-maker** | The deterministic rules engine sets urgency; the clinician decides. No model output sets, lowers or orders urgency |
| **Out of scope** | Diagnosis, differentials, prescriptions, doses, patient counselling, image interpretation as findings, unsupervised use, real patients before ethics approval and validation (`ETHICS.md` §6) |

## Components

| Component | Model or engine (pin) | Licence (as recorded in the repo) | Runs where | Default |
|:---|:---|:---|:---|:---|
| Text extraction (local) | **Qwen3-4B-Instruct-2507**, Q4_K_M GGUF, `unsloth/Qwen3-4B-Instruct-2507-GGUF@a06e946b`, SHA-256 `3605803b…c67e597`, 2.50 GB | Apache-2.0 | This laptop: loopback-only `llama-server` (Homebrew llama.cpp 0.5.0, build 11146), API key file, offline | Used when `AI_PROVIDER=local` (the owner's choice for extraction) |
| Text extraction and assistant (cloud) | **Azure OpenAI `gpt-4.1-mini`** on an Azure AI Foundry endpoint, through Semantic Kernel | Commercial cloud service under Microsoft's terms | Microsoft Azure | Only with `AI_CLOUD_ENABLED=1` and `AI_CLOUD_SYNTHETIC_DATA_ONLY=1` and an allow-listed host |
| Alternatives (local) | Gemma 4 E4B-it Q4_0 (`ggml-org/gemma-4-E4B-it-GGUF@b8093469`); Gemma 4 E2B-it Q4_0 (`@b4243c15`) | Apache-2.0 | This laptop | E4B rejected (see results); E2B pinned, not measured |
| Offline stand-in | `fake` provider: a deterministic keyword extractor, **not an LLM** | Project code | In process | Tests, CI and offline development |
| Red-flag candidates | Deterministic keyword suggester, **not a model** | Project code | In process | On (`AI_KEYWORD_RED_FLAGS=1`) |
| Protocol evidence lookup and search | Lookup by rule id plus an in-memory SQLite FTS5 index (BM25) over the curated corpus, **not a model**: no embeddings, no vector store, no network (`docs/23`) | Project code; passage texts keep their sources' licences (none stored yet) | In process | Always (case screen lookup); the search is not exposed by any route yet |
| Medical image description | **MedGemma 1.5 4B-it**, official gated `google/medgemma-1.5-4b-it` at revision `91850547…`, converted locally to MLX 8-bit, every file hash-checked | Health AI Developer Foundations terms (accepted by the owner, 2026-10-05) | This laptop, in the OCR worker | Off (`MEDGEMMA_ENABLED=0`); `fake`, Gemini and Azure backends also exist behind cloud, consent and synthetic-attestation gates |
| OCR: detection, recognition, orientation | PaddleOCR PP-OCRv6 small det/rec + mobile cls (RapidOCR 3.9.2, onnxruntime 1.20.1), hash-pinned | Apache-2.0 | In process | Off unless `OCR_ENABLED=1` |
| OCR: independent table reading | Surya OCR 2 GGUF (`datalab-to/surya-ocr-2-gguf@6a3a4c30`) | Code Apache-2.0; weights modified OpenRAIL-M (free for research, personal use and startups under $5M; check before commercial use) | OCR worker | `OCR_SURYA_ENABLED` |
| OCR: handwriting | Chandra OCR 2, converted to MLX 8-bit from `datalab-to/chandra-ocr-2@af93b47` | Code Apache-2.0; weights modified OpenRAIL-M (startups under $2M) | OCR worker | `OCR_CHANDRA_ENABLED` |
| Drug-name check | NLM RxNorm Current Prescribable Content (Sep 2026 release, MD5 verified), offline fuzzy match | No licence required (NLM) | In process | With OCR |
| Voice activity detection | Silero VAD 6.2.3 | MIT | In process | With voice |
| Speech recognition (local) | AI4Bharat IndicConformer-600M multilingual (22 scheduled languages incl. Hindi and Odia; **not English**) | MIT, gated | This laptop | `VOICE_LOCAL_ASR_ENABLED` |
| Speech recognition and read-back voice (cloud) | Sarvam Saaras v4 (speech to text), Bulbul v3 (text to speech) | Commercial API; Sarvam may retain content up to 30 days unless the organisation opts out | Sarvam cloud | Off; needs per-case `voice_cloud` consent |
| Translation | AI4Bharat IndicTrans2 `indic-en-dist-200M`, pinned revision with a SHA-256 manifest | MIT, gated | This laptop | Off (`TRANSLATION_ENABLED=0`) |
| PII detection | Microsoft Presidio analyzer + spaCy `en_core_web_sm` + project patterns for Indian identifiers | MIT | In process | Always, before any model |
| Guardrails runtime | NVIDIA NeMo Guardrails, running the project's deterministic detectors (no model inside) | Apache-2.0 | In process | Recommended on for demos (`GUARDRAILS_ENABLED=1`) |

Pins, hashes and licences are recorded in `backend/app/ai/local_models.py`, `backend/app/ocr/model_pins.py`, the
download scripts in `backend/scripts/`, and `docs/12`, `docs/14`, `docs/16`, `docs/18`. Check each licence at its
source before any commercial use.

## Measured results

All on **synthetic** cases written by the project team, who also wrote the labels: smoke tests, not clinical
evaluations. Every number keeps its denominator.

### Local extraction model (Qwen3-4B), 2026-10-05, Apple M5, 16 GB (`docs/16` §2a.4, §2c)

20 synthetic English cases (7 with no red flag), the real path: redaction → model (3 MAKER passes at 0.1/0.2/0.3) →
schema → grounding → vote. Gate agreed before measuring: grounding ≥ 70 % and median 3-pass time ≤ 30 s.

| Measure | Shipped configuration |
|:---|:---|
| Schema-valid passes | 60/60 |
| Grounding pass rate | 77 % (a second run that day: 76 %) |
| Values found | 19/21 (second run: 20/21) |
| Red flags found by the model alone | **0/15** |
| Red flags with the keyword suggester (what the reviewer sees) | **15/15**; 4 extra suggestions on positive cases; 0/7 negative cases with a suggestion |
| False raise on a negative case | 0/7 |
| Median / maximum time for 3 passes | 17.7 s / 25.8 s (second run: 17.5 s / 27.7 s) |

Keyword suggester on 24 held-out phrasings written before the rules: 21/22 found, 2/6 negative cases with a
suggestion on the first run; after two disclosed fixes (no longer held out) 22/22 and 1/6.

One-pass check on 9 synthetic sentences after grounding (`docs/plans/clinical-note-v2-plan.md`): terms 15/15, exact
durations 11/11, valid replies 9/9, never guessed an unverifiable number 4/4, **denied symptoms kept 1/4**, about 4.9 s
per sentence.

### Azure `gpt-4.1-mini`, 2026-11-12 (`docs/plans/semantic-kernel-orchestration-plan.md`)

Nine synthetic sentences through the same extraction path: terms 14/15 and 15/15 on two runs, durations 11/11,
denials 3/4, valid replies 9/9, about 2.5 s per sentence. Connection check through Semantic Kernel: ready in about 2.3 s.

### Rejected alternative

Gemma 4 E4B, same settings, 90 s timeout: 0/60 passes finished in time (about 1,000 tokens per pass). Not used.

### Encounter summary (deterministic; no model call), 2026-11-12

47 synthetic cases (11 image, 12 interview, 18 summary, 6 symptom), offline: fake extraction provider, replayed OCR
and image output. Every metric passed, for example: supported sentences 132/132, traceable facts 298/298, urgency
matching the rules engine 47/47, symptom recall 18/18 and precision 18/18, numeric values exact 7/7, handwriting kept
out of prose 4/4, image findings kept out of prose 4/4. Reproduce with `make eval-summary`. This measures the
summary's construction, not clinical accuracy.

### Medical image description (MedGemma 1.5, local), 2026-10-05 (`docs/18`)

One procedurally drawn picture (ellipses and arcs, not a radiograph): **described as a normal chest X-ray** ("lung
fields: clear") **with self-reported confidence 0.95**. First call 15.2 s, warm call 7.5 s. This is why image output is
a description for a clinician, confidence is shown only as a label, and acknowledgement is required before sign-off.

### Clinical assistant fence (scripted model; mock mode), 2026-11-13 (`docs/24` §7)

The full assistant on one synthetic visit, with scripted model replies through Semantic Kernel's real function-calling
loop (11 runs, all four questions): supported sentences kept 7/7 (compared exactly); unsafe or unsupported sentences dropped
33/33, each for the expected reason 33/33; tool calls outside the question's list refused 3/3; tool calls run all on the list
18/18.
This measures the fence, not the model: it says nothing about how often `gpt-4.1-mini` writes a useful answer.

### Deterministic safety components (no model), 2026-11-13 (`docs/25`)

These are measured by `make safety-eval` on synthetic, author-written cases. They are not clinician-labelled.

**Rules engine:**

- labelled urgency 71/71, of which RED returned as RED 45/45;
- under-triage 0/71;
- threshold probes 45/45;
- missing-data cases kept off GREEN 78/78;
- model suggestions that lowered urgency 0/284.

**Output guard:** blocked 27/34 unsafe sentences and let through 20/20 ordinary facts. It missed 4 diagnostic,
2 prescriptive and 1 urgency-lowering phrasing.

**Input rail:** flagged 12/14 instruction-like inputs and 0/12 ordinary ones. It missed a transliterated Hindi
instruction and "New instructions from the admin".

**Redaction:**

- kept 16/18 identifiers out of the model text (2 lower-case names missed);
- kept clinical values exactly in 5/6 sentences ("Hb 9.8" is taken for a name).

### Not yet measured

Real documents (OCR), native Hindi and Odia speech, translation quality, the clinical assistant on the real Azure
model, and any performance difference across language, age, sex or literacy. No component has been compared with
clinicians.

## Known failure modes

| Component | Failure | Mitigation in SEHAT |
|:---|:---|:---|
| Qwen3-4B | Misses red flags; drops denied symptoms; general-purpose, not medical | Keyword suggester; red-flag screen always asked by the rules engine; per-field human review |
| All models | Plausible but unsupported values | Schema validation, grounding against quotes, MAKER disagreement goes to a human |
| MedGemma | Confident descriptions of non-medical images; not intended for clinical decisions (its own model card) | Description only, guard on every field, acknowledgement before sign-off, ECG never sent |
| OCR engines | Misreads, footer fragments, handwriting errors | Self-verification, disputes kept, every value confirmed by a person |
| Speech | No English in IndicConformer; accents and noise | Read-back with explicit confirmation; typed entry always available |
| PII detection | Misses Indian names and unusual identifier formats (5 documented misses); spaCy takes "Hb 9.8" for a person's name and removes the value (`docs/25` §6) | Synthetic data only; cloud off by default; in the maternal scenario a missing Hb shows as missing information (elsewhere it disappears with no flag) |
| Output guard and input rail | Phrasings their patterns do not know: 7 of 34 unsafe sentences and 2 of 14 instruction-like inputs got through on the scorecard's synthetic cases (`docs/25` §4) | Grounding in quotes, template-built note, the assistant's own answer gate, raise-only urgency, named sign-off |

## Ethical considerations

See `ETHICS.md` (principles, Indian and international frameworks, patient rights, the go/no-go list before real
patients) and `GUARDRAILS.md` (every control and its limits).
