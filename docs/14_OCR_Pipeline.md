# 14 — OCR Pipeline (Phase 5): Decisions, Design, Verification

> **Research prototype, not a clinically validated device.** Document OCR is non-diagnostic. It reads
> printed lab reports, handwritten prescriptions and discharge summaries into *candidate* values. A named
> health worker must check the report belongs to the patient and decide every value. Reviewed values are
> shown to the reviewing health worker (a doctor view arrives with the Phase 8 dashboard) and **never set or change urgency**: the deterministic rules engine (docs/10)
> decides urgency from what a person enters on the triage form. SEHAT AI is "DPDP-ready by design" only;
> it does not claim CDSCO clearance or DPDP compliance.

Status labels (as docs/12): **tested-real** (a real engine ran in this repo's tests), **tested-mock**
(contract tests with a fake engine), **unverified**, **BLOCKED**, **unsupported**. §9 has the evidence.

---

## 1. Decision record

The pipeline follows the architecture (`sehat_ai_final_architecture__2.md` §10, §10A, stack table) and
docs/09 Phase 5. Where the architecture could not run as written on the target machine (Apple M5, 16 GB,
no NVIDIA GPU) a way was found to run it; where it conflicts with itself or with AGENTS.md, the decision
below was taken with the user (2026-10-01).

| Architecture says | What runs here | Why / evidence |
|---|---|---|
| Printed lab reports: "Surya / PaddleOCR (table-aware, CPU)" | **Both.** PaddleOCR (PP-OCRv6 det/rec, ONNX via RapidOCR 3.9.2, in-process) gives words, boxes and scores; **Surya OCR 2** (0.22.1, official GGUF via llama.cpp) gives an independent table-aware reading. | Surya 2 on Apple Silicon runs only through `llama-server` (Surya README). Models pinned by revision/SHA-256. |
| Handwritten Rx: "Chandra OCR 2 (4B, INT8 QAT, ~4 GB)" | **Chandra OCR 2**, official weights `datalab-to/chandra-ocr-2@af93b47` (5B params, BF16 10.6 GB; SHA-256 checked), **quantized locally to 8-bit with MLX** (4.8 GB, 9.05 bits/weight incl. scales). | Datalab ships only vLLM/HF backends; MLX runs it on Apple Silicon. **Post-training INT8, not QAT**: QAT needs ~1K labelled Indian prescriptions (architecture table), which do not exist here. |
| "Document Type?" classification step | The **reviewer selects the type** (docs/09 §5.1 selector: lab report / prescription / discharge summary; X-ray/ECG shown unavailable). No automatic classifier. | docs/09 specifies the selector; an automatic classifier would be another model whose mistakes silently change the route. |
| Discharge summary → Chandra (§10A); "Mixed → both engines → merge" | `discharge_summary` runs **both** paths: Chandra blocks give medications, and Chandra's **table** readings join Surya's as independent readings of each lab row (each engine compared separately; any disagreement = Dispute). Values are never merged into a new value. | |
| Quality check (blur, skew, lighting) → "ask patient to retake" | OpenCV checks on the stored page; poor → `422 DOCUMENT_QUALITY_LOW` with reasons. Small skew (0.5–7°) is straightened first and recorded. | Thresholds set on fixtures + degraded copies (`tests/ocr/test_quality.py`). |
| Gödel CoVe: word confidence < 0.7 → re-OCR at 2× → dispute | Implemented as written (`verify.py`); plus a cross-engine check (Surya vs PaddleOCR; Chandra vs PaddleOCR). | A same-engine re-read can repeat an error: agreement is labelled "agreement", never "verified". |
| RxNorm fuzzy match (≥ 0.8; < 0.95 uncertain) | Implemented **offline** on NLM "RxNorm Current Prescribable Content" (Sep 2026 release, MD5 verified, no licence needed). | No drug name leaves the machine. There is no official "RxNorm India subset": Indian brands are often "unknown". |
| Reference ranges, "12 common Indian tests" (docs/09 numbers + mild/moderate/critical) | `app/rules/reference_ranges.py`: 11 tests with a **cited source per number** (WHO 2024, MedlinePlus, ADA 2026, ICMR 2018, NCEP ATP III); bands only where a published grading exists (WHO Hb, CTCAE v5, ADA, NCEP). | AGENTS.md rule 8. **6 docs/09 numbers differed from their sources** (Hb, WBC, fasting glucose, ALT, TSH, uric acid) — source used, difference recorded in `DOCS_DIFFERENCE`. **Troponin I** has no universal limit (Fifth UDMI 2026) → printed range only. |
| §10A "Structured values → Rules Engine"; "Troponin > 0.04 → RED FLAG" | **Display and flag only** (user decision). Values never reach `evaluate_triage`. | The architecture also says (line 1617, and docs/06 :371, docs/07, docs/10 ADR-7) lab values are "not a rule input". Tested import boundary. |
| MAKER voting on Hb/platelets/creatinine | Reported as `not_run` | docs/09 assigns MAKER to Phase 6 (LLM multi-pass). |
| Azure AI Document Intelligence (Cognizant) | Not used | Cloud: needs Azure credentials and a cloud-consent purpose. Deferred. |
| MedGemma (X-ray/ECG description) | Not used; the upload type is shown as "not available" | "if time permits" (docs/08 #8). Deferred. |
| `POST /intake/document` (multipart, docs/06) | Implemented as specified; parsed **in memory** (no temp files). | docs/06 §3.3 response fields included, plus source-linked fields. |
| Documents on "local filesystem" | Re-encoded page PNGs in `data/documents/<case>/` (git-ignored); originals never stored (SHA-256 only). | User decision 2026-10-01. |

Rejected for this machine: none of the architecture's engines — all three run locally.
Licences: Surya and Chandra **weights** are modified OpenRAIL-M (free for research/personal use and
startups under $5M / $2M); code Apache-2.0. Check before any commercial use. PP-OCR models and RapidOCR:
Apache-2.0. RxNorm prescribable content: no licence required (NLM).

## 2. Architecture

```
ANM ─▶ /intake/documents?case= (Next.js) ─postForm─▶ same-origin proxy ─▶ POST /api/v1/intake/document (multipart, in memory)
 T1 [txn]  case access (creator ANM) → triage consent in effect AND granted under a notice that discloses
           documents (DOCUMENT_NOTICE_VERSIONS) → idempotency (case, key) → `pending` row + audit (before any engine)
 worker thread (≤3 uploads buffered at once, checked before reading the body; one document processed at a time; ≤2 waiting; else 503 OCR_BUSY):
   decode: magic bytes → header-only pixel cap → Pillow / PDFium in a killable subprocess → EXIF transpose,
           metadata stripped, ≤3000 px, deskew → stored page PNG (OCR runs on exactly these pixels)
   quality check (blur / skew / dark / contrast / blank) ──poor──▶ 422 "retake"
   route by document type:
     lab_report        → PaddleOCR (in-process) + Surya OCR 2 (local worker) → table rows → extract_lab
     prescription      → Chandra OCR 2 (local worker) layout blocks + PaddleOCR → extract_rx → RxNorm (offline)
     discharge_summary → both
   Gödel verification (verify.py) → per-field band, readings, checks; sourced + printed ranges
   page PNGs written to data/documents/<case>/
 T2 [txn]  row still pending & consent unchanged → pages, fields, audit ── else everything discarded (files deleted)
Reviewer: attestation ("is this the patient's report?") → per row confirm / correct / not sure / reject
          → GET /cases/{id}/documents/reviewed (view only; never a triage input)
```

**Local OCR worker** (`backend/ocr_worker/worker.py`, run by the backend with `.venv-ocr`): Surya 0.22 and
Chandra need `transformers` 5.x, the Phase 4 voice model needs `transformers` <5, so these engines live in a
separate environment. The worker:
- listens only on a **Unix socket, mode 0600**, in a 0700 directory; every request needs a token generated
  when the backend spawns it (passed by environment; never in `.env` or logs) — tested: wrong token → 401;
- keeps **one large engine resident** (Surya or Chandra) on the 16 GB machine;
- starts Surya's `llama-server` on 127.0.0.1 with a random `--api-key`;
- forces the Hugging Face hub offline and blocks non-loopback connections from its own Python sockets
  (a Python-level guard, not an OS sandbox; `llama-server` itself binds 127.0.0.1 only);
- is **killed** (with its `llama-server`, which Surya starts in its own session) when a call exceeds
  `OCR_PAGE_TIMEOUT_S` — real cancellation, unlike in-process threads. PaddleOCR runs in-process and can only
  be stopped between pages.

## 3. Privacy and consent

- **No new consent purpose**: everything runs on this server; no document, image or text is sent to any
  outside company. Triage consent is required **and** must have been given under a notice version that
  says documents are kept (`2026-10-01.2`): otherwise `403 CONSENT_NOTICE_UPDATE_REQUIRED` and the case
  re-consents. The new paragraph (en; hi/or drafts, docs/13) says a picture of each page and the text read
  from it are kept with the visit's record, read on this system only, and may show the patient's name.
- **Withdrawal during processing**: the result is discarded at T2, files deleted, `409 CONSENT_WITHDRAWN`
  (tested). **After withdrawal**: every read and decision is refused (403, audited). Re-consent re-opens
  stored documents (user decision, consistent with voice; tested).
- **Stored**: re-encoded page PNGs (EXIF/GPS/text chunks dropped — tested; files 0600 in 0700 folders), OCR text and boxes, extracted
  fields, decisions. **Not stored**: the original upload bytes (SHA-256 only).
- **Not done**: identifiers printed on the page (name, phone, ID numbers) are **not** redacted from the stored
  image or text.
- **Deletion and retention** (migration step 5):
  - *Reviewer deletion*: `DELETE /cases/{id}/documents/{doc}` by the creator ANM or an MO — allowed even after
    consent is withdrawn (a common reason to ask). It records an append-only `ocr_purge_events` row, then deletes
    the page files, OCR fields (text, values, regions, readings), attestations and review decisions (incl.
    corrected values), and clears the document row's content columns. Identity, type, upload hash, the purge
    event and the audit log remain as evidence that a document existed and was deleted. Repeating it returns
    `already_deleted: true`. A pending (in-progress) document cannot be deleted (409). Database triggers allow
    these deletes **only** after a purge event exists for that document (tested: direct deletes still abort).
  - *Retention*: `OCR_RETENTION_DAYS` **must be set** when OCR is enabled — a number of days, or `none` (kept
    until a reviewer deletes it); the app refuses to start otherwise. **The duration is a product/legal decision
    that has not been made**; no default is assumed. Expired documents are purged at startup and before every
    document request, so they are never served (tested).
  - *Failures*: a page file that cannot be deleted is counted (`files_failed`, audit outcome `failure`, no
    path or content) and retried at the next startup.
  - *Limits*: SQLite `secure_delete` is enabled for purges (deleted rows are overwritten in the database file),
    but **copies of the database file (backups, synced folders) are not covered**. Voice transcripts and other
    case data have no deletion yet (outside Phase 5). The consent notice still says recorded information "is kept
    for now" on withdrawal, which remains true unless a reviewer deletes the document; it was not changed.
- **Audit**: `ocr_document_started/processed/failed`, `ocr_attestation_recorded`, `ocr_review_resolved`, `ocr_document_deleted` with ids,
  enums, counts and size buckets only. Tests plant canary names/phones/values and assert they never appear in
  audit rows or logs.
- **Logs**: the Phase 5 canary test found that `aiosqlite` logs every SQL statement *with parameters* at DEBUG
  (case data, voice transcripts, OCR values) — a pre-existing gap. `aiosqlite`, `rapidocr`, `httpx`,
  `python_multipart` and `PIL` loggers are now held at WARNING in `app/main.py`.
- **Access**: upload — the case's creator ANM (Phase 5 limits upload to ANM: a patient's upload could not be
  reviewed, because only the creator ANM or an MO may decide and MOs cannot open `/intake/*` yet). Read —
  creator or any MO; **supervisors cannot read document content** (404). Decide — creator ANM or MO.

## 4. Engines and routing

Explicit and visible: every document records which engines ran and their status (`engines`), e.g.
`{"paddleocr": "ok", "surya": "failed:ocr_timeout"}`. There is **no silent fallback**: if Surya is off or
fails, PaddleOCR's reading stands alone and every field is capped at Amber (`single_engine`).
Prescription / discharge types need `OCR_CHANDRA_ENABLED=1` (else 503 `chandra_not_enabled`).

**Model inventory (what is actually loaded at runtime, 2026-10-01).** Runtime/library ≠ model: RapidOCR is the
runtime for PaddleOCR's PP-OCR models; Surya and Chandra are vision-language models served by llama.cpp and MLX.

| Engine (purpose) | Runtime | Model artifact | Version / pin | SHA-256 | Licence |
|---|---|---|---|---|---|
| PaddleOCR text detection | RapidOCR 3.9.2 + onnxruntime 1.20.1 (in-process) | `PP-OCRv6_det_small.onnx` | rapidocr 3.9.2 `default_models.yaml` | `090f04ab…1ff94f` | Apache-2.0 |
| PaddleOCR text recognition (words, boxes, scores) | same | `PP-OCRv6_rec_small.onnx` | same | `6f327246…c14884` | Apache-2.0 |
| PaddleOCR orientation classifier | same | `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | same | `e47acedf…6215c` | Apache-2.0 |
| Surya OCR 2 (independent table-aware reading) | `surya-ocr` 0.22.1 → `llama-server` (Homebrew llama.cpp 0.5.0, b11146), in the worker | `surya-2.gguf` + `surya-2-mmproj.gguf` | `datalab-to/surya-ocr-2-gguf@6a3a4c30` | `1f18abe1…51e05`, `98c05636…fb2c0` (= official HF LFS hashes) | code Apache-2.0; weights modified OpenRAIL-M |
| Chandra OCR 2 (handwriting, discharge summaries) | `mlx-vlm` 0.7.4 / `mlx` 0.32.3, in the worker | `chandra-ocr-2-mlx-8bit/model.safetensors` (converted here) | from `datalab-to/chandra-ocr-2@af93b47` (BF16 weights SHA-256 `0804568b…aa3847`) | `2bbc127f…0a646` (recorded at conversion in `SEHAT_ENGINE_MANIFEST.json`) | code Apache-2.0; weights modified OpenRAIL-M |

Integrity (fail closed): PaddleOCR files are checked against `SEHAT_OCR_MANIFEST.json` before loading, and the
recogniser must carry its own character list (otherwise RapidOCR would download one). The worker checks Surya's
GGUF files against the official hashes and Chandra's 8-bit build against its conversion manifest **before**
loading; a mismatch or missing file returns `503 OCR_UNAVAILABLE (model_integrity_failed / model_not_installed)`
(tested with substituted files). Nothing is downloaded at runtime (Hugging Face hubs forced offline; explicit
paths). **Chandra OCR uses post-training 8-bit quantization (MLX); QAT is deferred** until a CUDA GPU and
about 1,000 labelled Indian prescriptions are available — it is not part of the docs/09 Phase 6 scope, and no
official QAT script exists (checked 2026-10-02: no QAT code in `datalab-to/chandra`; the model repository
ships BF16 weights only). The 8-bit Chandra build is post-training quantization, **not** QAT; whether MLX conversion is
byte-reproducible has not been checked — the recorded hash protects the build that was tested.
Disk: the 9.9 GB original BF16 download (`models/ocr/chandra-ocr-2-bf16`) is only needed to re-convert; it is
kept until the project owner decides (§9.4).

## 5. Extraction and verification

**Coordinates.** Integer pixels `[x0, y0, x1, y1]` on the stored page PNG, origin top-left, `x1/y1`
exclusive, `page_index` 0-based. Each page records `width`, `height`, `png_sha256` and `transform`
(`source`, `exif_orientation`, `scale`, `rotation_applied_deg`, `pdf_render_scale`). OCR runs on the
stored pixels, so boxes need no re-mapping. The UI draws boxes as **percent of the page** (resize-proof;
`src/lib/evidence.ts`, tested at 320/768/1280 px). An invalid or missing box is never drawn ("Can't point
to this on the image — check the paper copy").

**Lab rows** (`extract.py`): columns from the report's own header (Test / Result / Unit / Reference range /
Flag); words assigned by left edge; repeated headers reset columns; no header → pattern parse, flagged.
**One region per role** (name, value, unit, range, flag), never a merged box; when a field is a whole OCR
line, the line box is used (word boxes are estimated and can sit inside the glyphs — measured).
**Grammar** (`parse.py`): exact decimal text; Western and Indian grouping (`1,50,000`); comparators; a
qualitative whitelist; **never repairs** — `1O.5`, `1,5`, `1.2.3` stay raw and unreadable.
**Prescriptions** (`rx.py`): form, drug name, strength, `1-0-1` pattern, frequency, duration from Chandra
lines; region = Chandra's layout block.

**Verification** (`verify.py`, architecture §10 steps, made explicit):
1. word score < 0.7 → re-read the value at 2× (same engine family) → differs = **Dispute**;
2. drug names → RxNorm: **matched** only on an exact ingredient (+ strength in an RxNorm clinical drug);
   brand, INN-synonym or fuzzy matches are at most **uncertain**; none ≥ 0.8 → **unknown**;
3. reference ranges: printed range and sourced range compared deterministically and conservatively
   (missing or unrecognised unit, unit mismatch, sex-specific without sex, comparator results that straddle a
   bound → `not_comparable`; one-sided sources say `not_below_cutoff` / `below_upper_cutoff`, never "within");
   printed vs sourced disagreement caps at Amber; printed flag vs printed range disagreement = Dispute;
4. MAKER → `not_run` (Phase 6).
Cross-engine: Surya's row (matched by test name) must agree on value, unit and range; Chandra's drug name
must appear in PaddleOCR's reading of the same block. Disagreement = Dispute, **both readings shown**.

**Meaning of the numbers** — `field_confidence` = min of the engine scores that apply; `overall_confidence`
= min over fields (never a mean). They are engine scores, **not probabilities that a value is correct**.
Bands: > 0.85 **Accept** (pre-filled suggestion, still unreviewed), 0.5–0.85 **Amber** (both readings, no
default), < 0.5 **human entry**. Caps at Amber: Dispute, single engine, unrecognized test, RxNorm not
matched, printed ≠ sourced range, line-level geometry. Missing checks never raise a band.

## 6. Review policy

1. **Attestation first**: "Is this report for the patient in front of you, for this visit?" (shows the
   report dates). Confirm/correct needs the latest answer to be *yes*; a later *no* / *not sure* hides that
   document's values from the reviewed list (events kept).
2. **Per row**: ✓ confirm (means *every shown field of the row was checked against the paper*), ✎ correct
   (result, comparator, qualitative, unit, printed range incl. `<`/`≤`, flag; strength / dose pattern for
   medicines — structured only, no free text; a printed `<`/`>` sign must be kept or explicitly set to `=`), ? not sure (stays unresolved), ✗ wrong / not on report. A wrong test or
   drug name is rejected, never re-mapped.
3. Confirm is refused (409 `CORRECTION_REQUIRED`) for disputed or unreadable rows, or if any printed part
   of the row has no valid box. A correction of such a row must include the result; a row whose test name
   cannot be shown cannot be corrected at all (reject it); parts not corrected must have been visible.
4. Each decision stores the page-image hash and region hash the reviewer was shown; a mismatch, or a stale
   `supersedes`, is `409 STALE_DECISION`. The machine reading is never overwritten (append-only).
5. Range comparison is shown on reviewed values and, as the architecture's "flag out-of-range values",
   on pending values labelled *machine-read, pending review*; wording is always "printed by the lab / may not
   fit this patient's age or sex / not a diagnosis".

## 7. API

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/intake/document/capabilities` | any signed-in | engines, readiness, limits; loads nothing |
| POST | `/intake/document` | case creator, ANM | multipart: `file`, `case_id` (UUID), `document_type` (`lab_report`/`prescription`/`discharge_summary`), `idempotency_key` (UUID) |
| GET | `/cases/{id}/documents` · `/{doc}` | creator, MO | fields, regions, readings, checks, band, ranges, attestation; docs/06 §3.3 block |
| GET | `/cases/{id}/documents/{doc}/pages/{n}/image` | creator, MO | `image/png`, `no-store`, `nosniff`; hash re-checked |
| POST | `/cases/{id}/documents/{doc}/attestation` | ANM, MO (triage access) | `{answer, supersedes}` |
| POST | `/cases/{id}/documents/fields/{field}/review` | ANM, MO (triage access) | `{outcome, correction?, supersedes, shown_png_sha256, shown_regions_sha256}` |
| GET | `/cases/{id}/documents/reviewed` | ANM, MO | confirmed/corrected values with source refs; "never a triage input" |
| DELETE | `/cases/{id}/documents/{doc}` | ANM, MO (triage access; allowed after consent withdrawal) | deletes images, text, values, decisions; idempotent; audited (§3) |

Errors: `FEATURE_DISABLED` 404 · `UNSUPPORTED_MEDIA_TYPE` 415 · `DOCUMENT_TOO_LARGE` 413 · `DOCUMENT_INVALID` 400
(`corrupt`, `encrypted_pdf`, `too_many_pages`, `too_many_pixels`, `empty`, `pdf_timeout`) · `DOCUMENT_QUALITY_LOW`
422 (`reasons`) · `CONSENT_REQUIRED` / `CONSENT_NOTICE_UPDATE_REQUIRED` 403 · `OCR_UNAVAILABLE` 503/500 · `OCR_BUSY`
503 · `OCR_TIMEOUT` 504 · `IDEMPOTENCY_CONFLICT` / `IN_PROGRESS` / `DOCUMENT_ABANDONED` / `CONSENT_WITHDRAWN` /
`STALE_DECISION` / `ATTESTATION_REQUIRED` / `CORRECTION_REQUIRED` 409 · `CORRECTION_INVALID` 400.
A `pending` row older than a derived TTL (pages × worker timeout + margins) is closed as `abandoned`; a startup
sweep also deletes page files no row refers to.

## 8. Configuration and install

Everything is **off by default**.

```bash
# main environment (PaddleOCR in-process; rapidocr declares the GUI OpenCV, so install it without deps)
uv pip install --python .venv/bin/python -r backend/requirements-ocr.txt
uv pip install --python .venv/bin/python --no-deps rapidocr==3.9.2
(cd backend && ../.venv/bin/python scripts/download_ocr_models.py)          # 3 ONNX models, 31.7 MB, SHA-256
# RxNorm (no licence): download RxNorm_full_prescribe_<date>.zip from NLM, check its MD5, then
(cd backend && ../.venv/bin/python scripts/build_rxnorm_index.py ../models/rxnorm/RxNorm_full_prescribe_09082026.zip)
# OCR-engine environment (Surya 2, Chandra 2)
uv venv --python 3.11 .venv-ocr && uv pip install --python .venv-ocr/bin/python -r backend/requirements-ocr-engines.txt
brew install llama.cpp
.venv-ocr/bin/python backend/scripts/download_ocr_engine_models.py         # ~12 GB download; 4.8 GB kept
```

`.env`: `OCR_ENABLED=1`, `OCR_SURYA_ENABLED=1`, `OCR_CHANDRA_ENABLED=1`, and **`OCR_RETENTION_DAYS`
(required when OCR is on)**: a whole number of days, or `none` (kept until a health worker deletes the document).
The server refuses to start with OCR on and no value. No value is chosen in this repository: the duration is a
product/legal decision for the organisation running the system. For a local synthetic demo use a prototype
value such as `OCR_RETENTION_DAYS=30` (or `90`) — this is a demo setting, **not a retention policy**.
Optional: `OCR_MODEL_DIR`,
`OCR_DOCUMENT_DIR` (default `./data/documents`), `OCR_WORKER_PYTHON` (`./.venv-ocr/bin/python`),
`OCR_MAX_BYTES` (10 MB, max 20 MB), `OCR_PAGE_TIMEOUT_S` (180), `OCR_RXNORM_DB`.
Editors pointed at `.venv` will flag `worker.py`'s imports as missing: it runs under `.venv-ocr`.

## 9. Verification status

Run 2026-10-01 on Apple M5, 16 GB, macOS, Python 3.11; synthetic documents only.

| Check | Command (from `backend/`) | Result |
|---|---|---|
| Backend suite before Phase 5 | `../.venv/bin/python -m pytest -q` | 626 passed, 9 skipped, 5 xfailed |
| Backend suite after (incl. deletion/retention and pre-commit fixes, 2026-10-02) | same | **811 passed, 10 skipped (opt-in live), 5 xfailed** (+185 tests: OCR, lifecycle, worker, safety boundary). Four existing tests edited on purpose: schema version 3→4 (2 files), the non-JSON-route boundary list (now also the OCR multipart route, whose fields must be file/UUID/enum), notice facts (+document paragraph). |
| Real engines, network blocked | `SEHAT_LIVE_OCR=1 ../.venv/bin/python -m pytest -m live tests/ocr -s` | 3 passed (below) |
| Real end-to-end over HTTP | scripted client against `uvicorn` with all three engines | PASS (below) |
| Frontend | `npm run lint`, `npx tsc --noEmit`, `npm test` (node --test, 5 geometry tests), `npm run build` | all pass |
| Re-run on `main` after PR #5 (2026-10-02) | backend suite; live (`SEHAT_LIVE_OCR=1`, network blocked); frontend lint, tsc, `npm test` (7: geometry + retake advice), build | **828 passed, 10 skipped (opt-in live), 5 xfailed**; live 3 passed; frontend all pass |

### 9.1 Results

| Path | Status | Evidence |
|---|---|---|
| Decoding, caps, PDF subprocess kill, EXIF, metadata strip, deskew, no temp files | tested-real | `tests/ocr/test_files.py` |
| Quality check | tested-real (synthetic degradations) | `tests/ocr/test_quality.py` |
| Grammar, extraction, per-role regions, dates | tested-real (pure code on replayed truth) | `test_parse.py`, `test_extract.py` |
| Gödel checks, bands, disputes, real Surya rows | tested-real (pure code; Surya output recorded from the real engine) | `test_verify.py` |
| Sourced reference table | tested-real | `test_reference_ranges.py` |
| API: consent/notice gate, withdrawal mid-run, idempotency, attestation, review, stale/shown-evidence, corrections, authz/IDOR, supervisors, canaries, no triage rows, append-only | **tested-mock** (fake engines replaying truth and recorded engine output) | `test_api.py` |
| PaddleOCR (real, offline) | **tested-real**: 26/26 values exact; 26/26 value boxes contain the printed value and touch no other row; 0.99 s/page | `test_live.py` |
| Surya OCR 2 (real, offline, local worker) | **tested-real**: lab table 6/6 rows; 13.8–15.4 s/page incl. start | `test_live.py`, HTTP run |
| Chandra OCR 2 8-bit MLX (real, offline, local worker) | **tested-real on ONE synthetic handwriting-style page only** (a font, not real handwriting): 4/4 medicine lines, patterns `1-0-1`/`1-1-1`/`1-0-0`; 23–28 s/page incl. load; peak 6.7 GB (MLX) | `test_live.py`, spike |
| RxNorm offline index (Sep 2026, MD5 verified, 33,037 names) | tested-real | HTTP run; `test_api.py` (tiny index) |
| Browser UI (upload, highlight, review) | **not run in a browser**; the full flow passed over the real Next.js proxy with real engines (32/32, §9.3); geometry verified by `node --test` and by drawing stored regions onto stored pages | |
| Real (non-synthetic) reports, phone photos, real handwriting | **not verified** — no governed dataset; must not use real patient documents | |
| Mobile camera capture | **BLOCKED**: needs HTTPS off localhost (as Phase 4) | |

**Real-engine HTTP run (2026-10-01).** CBC PNG: 6 rows, PaddleOCR and Surya agree on all, 15.4 s.
Two-page scanned PDF: 13 rows, 24.3 s — on page 1 **PaddleOCR misread the unit `g/dL` as `7p/6`; Surya read
`g/dL`; the row became a Dispute (Amber)**, the cross-engine check working on a real misread. Handwriting-style
prescription: 23.9 s — **Chandra wrote "Amoxicillin" where the page says "Amoxycillin"** (silent normalisation)
and PaddleOCR read "Amoxycillin … 1-7-7"; the row is a Dispute with both readings. "Ambroxol" (PaddleOCR
"Ambroxo1") also disputed; Pantoprazole matched RxNorm exactly (Accept); Paracetamol → RxNorm "uncertain"
(INN synonym of acetaminophen). Attestation → confirm → reviewed list with source regions; page image
`no-store`; supervisor 404. Whole test-process peak memory with all engines: 2.1 GB (backend) + worker.

**Measured cost.** Install: main venv +~100 MB; `.venv-ocr` ~2 GB; models 31.7 MB (PaddleOCR) + 1.47 GB
(Surya GGUF) + 4.8 GB (Chandra 8-bit; the 9.9 GB BF16 download can be deleted after conversion).

### 9.2 Spike gate (M1) record

Pre-registered thresholds were met: values 26/26 exact, 0 lost decimals, cold load 1.5 s, warm 0.95 s/page,
peak RSS 1.14 GB. The first geometry measure (IoU ≥ 0.5 against tight glyph boxes) **failed** (15/26, padded
OCR boxes); the revised containment criterion (box contains the printed value, centre on it, no other row)
passes 26/26 and is what the live test enforces. The second-reader model (`en_PP-OCRv5`) was **BLOCKED**
(ModelScope's China CDN times out from this network) and was then superseded by the architecture's Surya route.

### 9.3 Browser checklist — results (2026-10-01)

**No browser was driven**: browser automation was not available in this environment. Instead the whole flow ran
over the **same path a browser uses** — `POST /api/session` (httpOnly cookie) → Next.js dev server same-origin
proxy (`/api/backend/*`) → FastAPI → the three real local engines — with a scripted client
(`backend/scripts/e2e_ocr_proxy_check.py`, 32/32 PASS on 2026-10-01; re-run on `main` @ 672b472 on 2026-10-02 with
`OCR_RETENTION_DAYS=30` (demo value): **32/32 PASS**, lab 16.7 s, 2-page PDF 23.9 s, prescription 26.4 s). Page rendering, on-screen highlight placement, keyboard and focus are
therefore **NOT TESTED** in a browser.

| # | Checklist item | Result | Evidence |
|---|---|---|---|
| 1 | Log in as ANM | PASS (proxy) | session cookie set; `/intake/documents` served (200) |
| 2 | Consent notice; stale consent refused; upload blocked without consent | PASS (proxy) | `NOTICE_VERSION_STALE`; notice `2026-10-01.2` has the document paragraph; `CONSENT_REQUIRED` |
| 3 | Upload each synthetic type (PNG lab, 2-page PDF, handwriting-style PNG) | PASS (proxy, real engines) | 15.8 s / 23.0 s / 26.7 s |
| 4 | Unsupported, malformed, oversized, too many pages, blurry | PASS (proxy) | 415 / 400 corrupt / 413 / 400 too_many_pages / 422 blurry (retake) |
| 5 | Processing progress and failures shown | NOT TESTED (browser) | API errors verified; on-screen text needs a browser |
| 6 | Navigate between pages | PASS (API) / NOT TESTED (UI) | page images 0/1 served, page 2 → 404; fields carry `page_index` |
| 7 | Source highlights on the printed value | PASS (geometry) / NOT TESTED (screen) | every role inside the served page; stored regions drawn on stored pages land on the text (§9.1); `node --test` percent/hit-test at 320/768/1280 px |
| 8 | Agreement, disagreement, unreadable, low confidence | PASS (proxy + tests) | PDF Hb unit dispute; Amoxicillin / Ambroxol disputes; unreadable & low-score in `test_verify.py` |
| 9 | Correct a value with `<`; persists after reload | PASS (proxy) | `<11.3 g/dL` kept after reload; machine reading `11.2` unchanged |
| 10 | Missing / unknown units | PASS (proxy + tests) | HBsAg `unit_missing`, no unit invented; unknown/missing unit → `not_comparable` |
| 11 | Nothing reviewed without explicit action | PASS (proxy) | all rows `machine_read` after upload; confirm refused before attestation |
| 12 | OCR cannot trigger triage | PASS (proxy + tests) | `triage_runs` = 0 after review; identical triage result with/without reviewed OCR values |
| 13 | Unauthorised access blocked | PASS (proxy) | supervisor 404 (document + image), patient upload 403, no session 401; MO API 200 but `/intake/*` redirects MO (known Phase 8 gap) |
| 14 | Timeout / retry / error feedback | PASS (tests) / NOT TESTED (UI) | worker kill on timeout, restart, start-hang bounded (`test_worker_lifecycle.py`) |
| 15 | Layout, keyboard, focus, accessibility | NOT TESTED | needs a browser and a screen-reader pass |
| 16 | UI separates automated findings from reviewer decisions | PASS (API) / NOT TESTED (UI) | `review_status`, `basis`, `checks_apply_to`; UI strings in code review only |
| — | Delete a document | PASS (proxy) | idempotent; image 404 afterwards |
| — | Rejected upload explains why and stays explained (2026-10-02) | PASS (proxy) / NOT TESTED (UI) | 422 `DOCUMENT_QUALITY_LOW` + `reasons` through the proxy; the document card keeps `quality.reasons`; advice text unit-tested (`retake.test.ts`) |

**Manual steps still to run in Chrome** (≈10 min): start the backend with `OCR_ENABLED=1 OCR_SURYA_ENABLED=1
OCR_CHANDRA_ENABLED=1 OCR_RETENTION_DAYS=none` and `npm run dev`; log in as ANM; create a case, record consent;
open "Add a lab report or prescription"; (a) upload `backend/tests/fixtures/ocr/cbc_low_platelet.png` — a
"Reading the document…" message appears, then rows; (b) answer "Yes, this patient's report"; (c) click *Platelet
Count* — magenta boxes labelled test/result/unit/range/flag sit on the printed text; resize the window to ~375,
768 and 1280 px and check they stay on the text; (d) confirm it; correct *Haemoglobin* with sign `<` and 11.3 —
"Corrected by reviewer" and the original reading still shown; reload — still there; (e) upload
`two_page_scan.pdf` and select a page-2 row — the page switches; (f) upload `rx_handwritten.png` as a
prescription — Amoxicillin shows "Two different readings" and confirm is off; (g) tab through the review
controls — every button reachable, focus visible; (h) "Delete this document" asks for confirmation, then shows
"Deleted"; (i) log in as supervisor and open a document image URL — refused.

### 9.5 Real-image testing and fixes (2026-10-02, PR #5)

Sample images supplied by the project owner (a printed lab report, two handwritten prescriptions, two discharge
summaries) were uploaded through the running app, processed locally, and deleted afterwards; none is in the
repository. This is a smoke test of five images, **not an accuracy evaluation** (docs/15 still applies).

| Found | Fix | Evidence |
|---|---|---|
| After the first 2× re-read, every later upload failed `500 internal_error`: RapidOCR keeps call arguments as instance state, so detection stayed off | every call sets all step flags; text without boxes is an engine error | `test_engine_state.py` (stand-in with the same state semantics; real-engine read→re-read→read checked) |
| Internal errors were invisible | the class name and app code location are logged, never message text | `test_engine_state.py` (canary) |
| Numbered handwritten items (`①`, `(2)`), form after the name, frequency in words, duration on the next line were dropped | parser accepts them, still needing a dosing signal | `test_rx.py` |
| Chandra read a handwritten 800 mg as 500 mg; RxNorm then reported a strength match | the strength is cross-checked against PaddleOCR; disagreement is a Dispute | `test_rx.py` |
| Discharge-summary results written as prose ("Serum Sodium:132 mmol/L, …") were not extracted | prose pairs from Chandra's reading, exact lexicon names only, cross-checked against PaddleOCR, line-level region (Amber at most) | `test_prose.py` (synthetic text; **no real full-resolution summary tested yet**) |
| On 375-px pages PaddleOCR misread digits (132→152) and Chandra looped and invented content (wrong age, a diagnosis not on the page) | pages whose median OCR line box is < 13 px are rejected for retake (`text_too_small`) before the slow engines; a looping Chandra reading is never used (prescription → `chandra_degenerate_output`; discharge summary → Chandra's text dropped) | `test_unreadable_guards.py`; real pages rejected in 1–2 s |
| A killed backend left the OCR worker running | the worker exits when its parent process is gone | `test_worker_lifecycle.py` |
| The rejection reason vanished from the screen | plain-language advice per reason, kept on the rejected document's card | `retake.test.ts`; not yet seen in a browser |

Outcome on the five images: printed report → 24 rows for review; full-resolution prescription → both
medicines, both disputed and Amber; discharge cover card → 0 values (correct: none printed); two low-resolution
pages → rejected with "text too small". The 13 px threshold rests on few pages (12 px failed, 14 px read well).

## 10. Known limitations

- Synthetic fixtures prove pipeline behaviour, **not accuracy** on real reports. Any accuracy evaluation needs
  a governed dataset and review process.
- The lab parser targets single-table printed layouts; method lines under names, units inside the range
  column, multi-panel pages, curved phone photos and Indic-script reports will produce misses or disputes.
- **Printed lab reports are the primary, demo-ready path. Handwriting (prescriptions) and discharge summaries are
  experimental**: tested on one synthetic page and two real-world-style images; Chandra misread a dose there.
- Chandra (a VLM) can silently normalise spelling, misread digits, and on unreadable pages loop and invent
  content; it is never trusted alone (cross-checked, disputes shown) and a looping reading is discarded — the loop
  detector is a heuristic (repeated blocks), not a guarantee against subtler invention.
- The text-size gate (median line < 13 px) is calibrated on a handful of pages; it may reject some readable
  small-print pages or pass some unreadable ones. Prose results are taken only for exact lexicon names.
- PaddleOCR misreads handwriting digits (1→7); a dispute is shown, but two engines can also agree on a wrong
  value — a human must check every row.
- Scores are engine outputs, not probabilities. Agreement is not correctness.
- In-process PaddleOCR cannot be pre-empted mid-page; the worker engines can (killed on timeout).
- Stored page images keep printed identifiers (no redaction). Deletion exists (reviewer request; retention once a
  duration is chosen) but does not reach copies of the database file (backups).
- RxNorm is a US terminology; Indian brands are often "unknown". No SNOMED codes (`snomed_code: null`): codes
  could not be verified on the official browser.
- The reference table assumes non-pregnant adults; sex-specific limits are compared only where both sexes agree.
- MOs cannot open `/intake/*` (Phase 8 dashboard), so review in this UI is by the creator ANM.

## 11. Deferred

MAKER voting (Phase 6) · triage-form wiring of reviewed values (Phase 7) · MO review UI (Phase 8) · MedGemma
image description · Azure Document Intelligence (cloud, consent purpose) · deletion for voice transcripts and other case data, and from backups · the retention duration decision · identifier redaction on stored images · real-photo and real-handwriting evaluation · QAT for Chandra ·
a verified SNOMED/LOINC mapping · Hindi/Odia notice review (docs/13).

## 12. Final council review (2026-10-01)

Five reviewers read the implementation (security, clinical safety, correctness, reviewer UX, architecture fit).
Material findings fixed and tested:
- a typed correction could drop a printed `<`/`>` (now required, or explicitly `=`);
- missing/unknown units and one-sided source cut-offs could yield "below/within range" (now `not_comparable` /
  `not_below_cutoff` / `below_upper_cutoff`);
- corrections did not require the test name/flag to have been visible;
- a hanging worker start could block all OCR (non-blocking handshake);
- a malformed Surya reply failed the document (now recorded as `failed:bad_response`);
- partial page writes and cancelled requests could leave files (now removed);
- page files were 0644 (now 0600 in 0700 folders);
- unbounded concurrent upload buffering (now ≤3, checked before reading the body);
- consent check and content read in separate transactions (now one read snapshot);
- the worker's network guard is documented as Python-level only;
- the docs/06 block now flags sourced-range results too; Chandra tables join the discharge-summary cross-check;
- UI: neutral "○" for unreviewed rows, separate confirmed/unsure/rejected counts, sign and unit correction,
  camera hidden without HTTPS, wording for MO access.

## 13. Plan for a governed real-document evaluation (not done; required before real patient data)

Moved to **[docs/15 — OCR Evaluation Plan](15_OCR_Evaluation_Plan.md)**: governance checklist, stratified
sampling, two-annotator protocol with adjudication, metrics, proposed acceptance criteria (pending clinical-lead
sign-off) and failure analysis. The synthetic fixtures are a regression fixture, not a clinical evaluation; no
real-world accuracy figure exists for this pipeline, and none may be quoted.

## 14. Demo guidance

- **Primary path**: the synthetic printed lab report `cbc_low_platelet.png` (ANM → consent → "Add a lab report
  or prescription" → upload → "Yes, this patient's report" → select *Platelet Count* to show the highlighted
  source → confirm). Engines: PaddleOCR + Surya, ~15 s.
- **Disagreement is a feature**: upload `two_page_scan.pdf` (PaddleOCR misreads a unit; Surya disagrees → "Two
  different readings") or `rx_handwritten.png` (Chandra silently writes "Amoxicillin" where the page says
  "Amoxycillin" → dispute). Say: the system caught it and a human decides; agreement would not have proved it right.
- **OCR never changes triage**: show the banner and the reviewed list; triage urgency comes only from the triage
  form and the deterministic rules engine (docs/10).
- Say "synthetic test documents"; never quote an accuracy figure; do not say "verified" or "validated".
- Present handwriting (prescriptions, discharge summaries) as **experimental**. A small or low-resolution photo
  is refused with "text too small" — that is the intended behaviour, not a failure to hide.
- First run of the day: the worker loads Surya (~3 s) and Chandra (hash check + load, ~10–30 s).

### 12.1 Pre-commit council (2026-10-02)

Fixed before commit: a race between two purges of the same document (now re-checked under the write lock;
tested), attestation of a deleted document (404; tested), post-purge trigger now freezes every evidence column
(tested), docs/09 ticks for browser-only items changed to `[~]`, the 32-step HTTP checklist moved into the repo
(`backend/scripts/e2e_ocr_proxy_check.py`), and §10/§11 no longer contradict the new deletion feature.

**Unexplained test observation:** one OCR test failed once while the real-engine live tests were running at
the same time; its name was not captured. It did not reproduce in five later runs of `tests/ocr` (three normal,
two with the CPU saturated by ten busy processes). Treat it as an open, possibly timing-related flake until it is
seen again with its name.
