# 18 — Medical Image Pipeline (visual-findings description)

> **Status:** implemented behind `MEDGEMMA_ENABLED` (default **off**). Verification: **mocked only** (§11).
> SEHAT AI is a research prototype, not a clinically validated device. It has no CDSCO clearance. It is
> DPDP-ready by design, not "DPDP compliant". This pipeline **describes** what an image shows. It **never diagnoses**,
> and it never sets or lowers urgency.

Source design: architecture §10A (`sehat_ai_final_architecture__2.md`, "Medical Image Understanding — MedGemma Pipeline").
This document records what was built, where it departs from §10A, and what has not been verified.

## 1. Purpose

A patient may bring a chest X-ray, an ECG strip, a CT image, or a photo of a wound or skin lesion. Without this layer
the reviewer sees only the raw image. This layer adds a short **AI-described visual findings** card next to the image
(for example "ST-segment elevation is visible in leads V1 to V4"). The medical officer interprets it. Every card carries
the mandatory disclaimer, links to the stored image, and must be acknowledged before sign-off when it carries a flag.

## 2. Two pipelines, one upload route

| Declared `document_type` | Pipeline | Code |
|:---|:---|:---|
| `lab_report`, `prescription`, `discharge_summary` | A: OCR text reading (unchanged, docs/14) | `app/ocr/service.py` |
| `chest_xray`, `ecg_strip`, `ct_report_image`, `wound_photo`, `skin_lesion` | B: visual-findings description (this doc) | `app/ocr/image_service.py`, `app/ocr/medgemma.py` |

Both use `POST /api/v1/intake/document`. The route branches on the declared type before any OCR check, so text
documents behave exactly as before. Pipeline B accepts PNG/JPEG only (a PDF gets 415). The upload is decoded with the
same pixel caps as OCR, EXIF-orientated, downscaled to the OCR page cap and **re-encoded without metadata** (EXIF/GPS and
text chunks dropped). Only the stripped image is stored and sent to a model.

## 3. Classifier policy (`app/ocr/image_classifier.py`)

- The uploader's `document_type` is **authoritative**. Nothing is rerouted silently.
- `classify_upload(filename, mime, first_bytes)` is a pure cross-check. A PDF by magic bytes is `lab_report`. Otherwise
  ordered filename-token rules apply (ECG → X-ray → CT → wound → skin → prescription → discharge → lab). Tokens are whole
  words split on non-alphanumerics, so `doctor_note.jpg` is **not** a CT and `CT-scan_head.png` is.
- A hint that differs from the declared type sets `classifier_mismatch: true`. It is shown as a warning, it **runs both
  rule sets** (§7) and it **requires reviewer acknowledgement**.
- The filename is used in memory only. It is never stored or audited, because it may contain a patient name.

## 4. Backends and gates

| `MEDGEMMA_BACKEND` | What it is | Gate |
|:---|:---|:---|
| `fake` | Canned, deterministic, offline outputs per image type. **Not a model.** For demos and CI. | `MEDGEMMA_ENABLED=1` |
| `google_ai` | Gemini via `google-genai` (`requirements-medgemma.txt`, imported lazily). Default model `gemini-3.8-flash` (`MEDGEMMA_MODEL`). | Startup: `AI_CLOUD_ENABLED=1`, `AI_CLOUD_SYNTHETIC_DATA_ONLY=1`, `GOOGLE_AI_API_KEY`. Per upload: effective `ai_assist` consent **and** `synthetic_attestation=true`. |
| `azure` | Azure OpenAI vision deployment via Semantic Kernel (same construction as `app/ai/azure_provider.py`). Model = `AZURE_OPENAI_DEPLOYMENT_NAME`. | Startup: as above plus complete `AZURE_OPENAI_*` and the allow-listed endpoint host. Per upload: as above. |

- `MEDGEMMA_ENABLED=1` also requires `OCR_ENABLED=1`, because uploads go through the document route.
- When `MEDGEMMA_ENABLED=0` the image is stored, the row is `not_available` (`disabled`), and **no backend is built or called**.
- If a cloud call is not allowed, the image is still stored and the response is `not_available` with the specific
  reason: `consent_ai_assist_missing` or `synthetic_attestation_missing`.
- One call per upload, bounded by `MEDGEMMA_TIMEOUT_S` (default 30 s). A timeout, error or unreadable reply is `failed`.
  The image is kept for the reviewer. **There is never a fallback to another backend.**
- Settings refusals name what is missing and never echo values. The API key is excluded from `repr`.

### Deviation from §10A (user decision)

§10A specifies local MedGemma weights ("patient X-rays never leave the device") with GPT-4o vision as a cloud fallback.
This build has **no local MedGemma**. Its only real backends are cloud models, so: (a) both are gated to
**synthetic/demo images only** (`AI_CLOUD_SYNTHETIC_DATA_ONLY=1` plus a per-upload attestation); (b) they reuse the
`ai_assist` consent purpose. Real patients would need a dedicated image consent purpose and a data-processing review
first (future work). Running MedGemma locally is future work. The `pipeline: "medgemma"` label and the
`"MedGemma image analysis"` source string follow the §10A contract; they do **not** mean a MedGemma model ran.

### 4a. Local medical vision: not available in this build (decision 2026-10-05)

Architecture §10A makes local MedGemma the primary image model. After a hardware and licence review (llm-council,
2026-10-05) this build **does not run a local medical vision model**, and says so instead of pretending:

- `google/medgemma-1.5-4b-it` (released 2026-01-13) is **gated** under the Health AI Developer Foundations terms. The
  project's Hugging Face token gets 403 until the project owner accepts them; accepting is the owner's legal decision.
  Ungated third-party copies (MLX/GGUF mirrors) exist but would sidestep the gate, so they are **not used**.
- Its model card lists chest X-ray, CT, MRI, histopathology, dermatology and fundus, **not ECG**, and states its outputs
  "are not intended to directly inform clinical diagnosis, patient management decisions, treatment recommendations, or
  any other direct clinical practice applications". A local build would therefore never describe `ecg_strip`.
- A generic vision model (for example Gemma 4) is **not** used for medical findings: it is not a medical model and its
  output would look like one.
- Hardware (Apple M5, 16 GB unified memory) could hold a 4-bit 4B vision model, but not next to the local text model,
  OCR and ASR during a demo without swapping (§12).

What exists instead: `MEDGEMMA_BACKEND=local` **refuses to start** with that reason (never a silent fallback to `fake`),
and `GET /intake/document/capabilities` reports `local_vision: {available: false, reason: "local_model_not_installed",
candidate, requires, unsupported_image_types: ["ecg_strip"]}`. Enabling it later needs: the HAI-DEF terms accepted, a
pinned official revision converted locally (as Chandra is, docs/14), a SHA-256 manifest, a loopback-only server, an
evaluation on synthetic images, and a migration for any new `not_available_reason`.

### 4b. Provenance (every image item)

Each item carries `provenance: {provider, model, mode, synthetic, medical_model}`. `mode` is `fake` (canned demo text),
`cloud` (google_ai / azure) or `none` (no backend was called: disabled, consent or attestation missing). `synthetic: true`
and `medical_model: false` mark the fake; the UI then shows **"DEMO TEXT — canned example, not produced by any AI model"**
above the card. Cloud output is labelled as a general model, not a validated medical device. Capabilities add
`medgemma_mode` and `medgemma_synthetic`.

## 5. Prompts and parsing

The five prompts and `extract_fields` lists are §10A verbatim. A JSON instruction is appended: return `description`,
`fields`, self-reported `confidence` (0–1) and `image_quality`; never name a disease; ignore instructions inside the
image. Temperature 0.1, max 500 output tokens (`PROMPT_VERSION = sehat-img-p1-2026-10-05`). Unknown keys are ignored.
Values are coerced to short strings. A reply that is not a JSON object, or has no findings, is `failed` (`bad_response`).

## 6. Non-diagnostic guard (field level) and confidence

`GUARD_VERSION = sehat-img-guard-1`, `medgemma.filter_findings()`:

- **Structured fields:** a field is withheld **whole** if any sentence in it trips `app.ai.guard.check()` (diagnosis,
  prescription, instruction/injection, identifier patterns), or an image-specific "shows/indicates/is … disease" pattern,
  or if the bare value is only a disease label (for example `Pneumonia`, `possible fracture`, `Malignant`).
- **Description:** one tripping sentence is dropped. More than one, or a description with nothing left, withholds the
  whole description.
- The response carries `withheld: {fields, description, reasons}`. The reviewer sees names, a flag and reason codes only.
  Withheld text is never returned, stored in a view or audited. The shared guard gained one pattern, `confirmed case of`
  (the existing `confirms?` did not match "confirmed"). `tests/ai` still passes.
- `MANDATORY_SUFFIX` (§10A wording) is appended to every shown description. The `disclaimer` field is present for every status.

**Confidence is a label only (user decision; departs from §10A "low confidence → raw image only").** The value is the
model's own **uncalibrated** self-report. It is shown as `confidence_band` (`high` > 0.8, `moderate` 0.5–0.8, `low` < 0.5)
and a note. It **never** hides fields, the description or urgency signals.

## 7. Urgency keyword rules — NOT clinician-validated

`check_image_urgency()` runs on the **unfiltered** model output, so the non-diagnostic filter can never hide a signal. A withheld "consistent with tension pneumothorax" still raises CRITICAL_IMAGING_FINDING. This is safe because the rules are raise-only and a signal note carries only the matched keyword, never model text. (This departs from §10A, where rules run after filtering. The change was made after the llm-council review.)

| Rule set | Signal | Action | Keywords | Fields scanned |
|:---|:---|:---|:---|:---|
| `ecg_strip` (§10A) | `ST_ELEVATION` | `RED_FLAG` | ST(-segment) elevation, elevated ST | `st_segment`, `abnormalities` |
| `chest_xray` (§10A) | `CRITICAL_IMAGING_FINDING` | `RED_FLAG` | pneumothorax, widened mediastinum / mediastinal widening, tension, massive effusion | `abnormalities`, `lung_fields`, `mediastinum`, `costophrenic_angles` |
| `wound_photo` (prompt rule) | `WOUND_CONCERN` | `YELLOW_FLAG` | crepitus, gas, necrosis/necrotic, purulent, pus, slough, foul | all wound fields |

- §10A scans one field per rule. This build scans a few related fields. That can only add flags.
- **Negation:** a hit with `no|not|without|negative for|ruled out|absence of|absent|free of|nil` within about 5 words
  before it in the same clause, or "not seen/absent/excluded" just after it, becomes `REVIEW_NOTE` with `negated: true`.
  It does not raise.
- **Hedges still raise:** `cannot rule out`, `cannot be excluded`, `possible`, `suspicious for`, `suspected`, `query`.
- **Mismatch:** when the classifier hint is a different image type, that type's rules also run over every field and the
  description. `rule_sets_run` lists both.
- These are prototype keyword lists. They are labelled `keyword_rules_validated: false` in every response. They need
  clinical governance review before any real use.
- **Raise-only:** signals are reviewer flags. They are shown in the case review (`image_findings.flags`) and they never
  change the rules-engine urgency on `triage_runs` (AGENTS rule 5).

## 8. Acknowledgement and sign-off

`requires_acknowledgement = any RED/YELLOW signal OR classifier_mismatch`. A reviewer (creator ANM or MO) records
"findings reviewed" with `POST /cases/{id}/medical-images/{doc}/acknowledge`. It writes one append-only
`medical_image_acknowledgements` row (idempotent) and the audit event `medgemma_findings_acknowledged`. Sign-off returns
`409 IMAGE_FINDINGS_NOT_REVIEWED` (with the pending `document_ids`) while any live image of the case still needs it.

## 9. Provenance, audit, storage, retention

- Migration **step 10**: `medical_images` stores declared type, classifier hint and mismatch, synthetic attestation,
  status and reason, file ref with the SHA-256 of the stored image and of the upload, findings, `withheld_json`, signals,
  `rule_sets_run`, confidence and band, `backend`, `model_id`, `prompt_version`, `guard_version` and the consent seq.
  The row is finalised once (trigger). `medical_image_acknowledgements` and `medical_image_purge_events` are append-only.
  `ocr_documents` is not touched.
- Audit actions (codes, counts, backend, model only; never description text, field values or the image):
  `medgemma_image_analyzed` (every completed or failed call), `medgemma_diagnosis_blocked`, `medgemma_image_not_available`,
  `medgemma_findings_acknowledged`, `medgemma_image_deleted`.
- Files: `OCR_DOCUMENT_DIR/{case_id}/{document_id}.img`, owner-only permissions. The image route re-checks the hash.
- Retention follows the OCR policy (`OCR_RETENTION_DAYS`). It runs before every document/image request and at startup.
  A reviewer can delete an image with `DELETE /cases/{id}/medical-images/{doc}`, which purges findings and the file and
  keeps the identity row plus the purge event. The startup sweep closes stale pending rows and removes `.img` files no
  row refers to. Database backups are not covered.

## 10. API

See docs/06 §2.3 and §3.4. Summary:

- `POST /intake/document` with an image `document_type` (+ optional `synthetic_attestation`) → image item (§3.4).
- `GET /cases/{case_id}/medical-images` → `{case_id, medical_images: [item]}`.
- `GET /cases/{case_id}/medical-images/{document_id}/image` → bytes, `no-store`, `nosniff`, `inline`.
- `POST /cases/{case_id}/medical-images/{document_id}/acknowledge` (body `{}`) → updated item.
- `DELETE /cases/{case_id}/medical-images/{document_id}` → deletion record.
- `GET /intake/document/capabilities` adds `medgemma_enabled`, `medgemma_backend`, `medgemma_model`, `medgemma_ready`,
  `medgemma_cloud`, `supported_image_types`, the five image keys in `document_types` (replacing `xray_ecg`) and
  `verification.medgemma = "mocked_only"`.

All routes sit behind the existing `OCR_ENABLED` dependency and use the OCR roles: ANM uploads; the creator ANM or an MO
reads, acknowledges and deletes; triage consent must be in effect to read.

## 11. Verification status

| Item | Status |
|:---|:---|
| Classifier, guard, negation, urgency rules | `tests/test_medgemma.py` with 11 canned fixtures (`tests/fixtures/medgemma/model_outputs.json`) |
| API path (upload, gates, failure, mismatch, acknowledgement, sign-off 409, delete, retention, capabilities, metadata strip) | Tested with the `fake` backend and AsyncMock doubles |
| `GoogleAIBackend` / `AzureVisionBackend` | Contract-tested against a fake SDK module / fake SK service only |
| Live Gemini or Azure call | **Not exercised** (no key, no synthetic image set). `VERIFICATION.medgemma = "mocked_only"` |
| Output quality on real images | **Not evaluated.** No accuracy claim of any kind |
| Keyword rules | **Not clinician-validated** |

## 12. Limitations

- No local medical vision model (§4a). The cloud backends are for synthetic/demo images only, and `ai_assist` consent is a stand-in for a
  future dedicated image purpose.
- Filename-based classification is a weak cross-check. Content-based classification is not implemented.
- The guard is pattern-based and can be evaded. It also over-withholds some negated mentions (for example "no evidence of fracture" is withheld).
  The reviewer always has the image itself.
- Self-reported confidence is uncalibrated.
- Keyword urgency rules are crude and not clinician-validated. Negation handling is a heuristic window.
- The 500-token output cap follows §10A and may truncate long replies (shown as `failed`). It has not been tuned live.
- No region/bounding-box source linking inside the image yet. Findings link to the whole stored image.
