---
name: sehat-real-app-check
description: Use when a change to SEHAT AI's document OCR, verification queue, documents screen, encounter summary or patient registry must be confirmed in the real running app (not only tests). Runs a synthetic lab report through the real local OCR engines and drives the documents screen and the medical officer's summary in headless Chrome, on a scratch database and scratch ports.
---

# SEHAT AI real-app check

Runs `backend/scripts/e2e_real_app_browser_check.py`: PASS/FAIL checks for the lab parser, "What to check, in order"
(C/U/R keys, auto-advance), the summary's unmatched documents, machine agreement, withheld content and evidence status,
medical officer verification on `/dashboard/verify`, and the patient registry (patient code issued, mistyped, checked,
linked, viewed and unlinked). The script's docstring is the source of truth for its commands.

## Rules

- **Scratch only.** A scratch `DATABASE_PATH` and `OCR_DOCUMENT_DIR`. Never `sehat.db`, real documents or real patients.
- **Synthetic sample.** The default sample is `public/med_img/test imag.jpeg`, which exists only in the main checkout
  (untracked). From a worktree, pass the sample's absolute path as the second argument.
- **Scratch ports and address.** Backend 8010, frontend 3010, and open `http://127.0.0.1:3010`, not `localhost`.
  The owner's own servers may be on 8000/3000. Check first: `lsof -iTCP:8010 -sTCP:LISTEN` and the same for 3010.
  Never stop a server you did not start.
- **No global installs.** Playwright goes into a throwaway venv and drives the installed Google Chrome
  (`channel="chrome"`), so no browser is downloaded.
- **No secrets.** Never read or print `.env`.

## Steps

1. Scratch folder: `S="${TMPDIR:-/tmp}/sehat-ui-check"; mkdir -p "$S/docs"`.
2. Backend, from `backend/`, in the background (`$PY` is the project venv's python, see skill `sehat-run-tests`):
   ```bash
   ENVIRONMENT=development ALLOW_UNSCOPED_DEMO=true DATABASE_PATH="$S/sehat-ui.db" OCR_DOCUMENT_DIR="$S/docs" \
     AI_PROVIDER=fake MEDGEMMA_ENABLED=0 VOICE_ENABLED=0 GUARDRAILS_ENABLED=0 TRANSLATION_ENABLED=0 AI_CLOUD_ENABLED=0 \
     "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8010
   ```
   Poll `http://127.0.0.1:8010/api/v1/health` until it answers.
3. Frontend, from `frontend/`, in the background (dev server: the production session cookie is Secure):
   ```bash
   NEXT_PUBLIC_API_URL=http://127.0.0.1:8010/api/v1 npx next dev -H 127.0.0.1 -p 3010
   ```
   Poll `http://127.0.0.1:3010/login`.
4. Playwright in the scratch venv: `uv venv "$S/pw" && uv pip install --python "$S/pw/bin/python" playwright`.
5. Run from inside `$S`, so screenshots land there:
   `cd "$S" && "$S/pw/bin/python" <repo>/backend/scripts/e2e_real_app_browser_check.py http://127.0.0.1:3010 "<sample.jpeg>"`
6. **Look at the screenshots** (`real_app_1_queue.png`, `real_app_2_summary.png`, `real_app_3_mo_verify.png`,
   `real_app_4_patient_code.png`). A PASS line is not a substitute for seeing the page.
7. Clean up: stop the two servers you started (`lsof -ti:8010 -sTCP:LISTEN | xargs kill`, same for 3010) and delete `$S`.

## Known behaviour (not failures)

- **Dashboards are role-specific.** The ANM's dashboard is the intake start page. The case workspace with the summary
  belongs to the medical officer (`mo_demo`). `/dashboard/verify` is for the medical officer only
  (`frontend/src/lib/auth.ts` ROUTE_ACCESS).
- **Voice 404s are expected** with `VOICE_ENABLED=0`; the script ignores those console errors.
- **Real OCR is slow:** minutes per page on a laptop; the upload wait allows up to 10 minutes.
- **Two footer fragments survive** on the sample ("idn e ( e", "www.labsmartl"). A known limit, not a failure.
