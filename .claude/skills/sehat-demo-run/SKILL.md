---
name: sehat-demo-run
description: Use to start, rehearse or check SEHAT AI's live demo stack on the demo laptop — local Qwen model, NeMo Guardrails, backend and frontend on a scratch database, offline — and to run the pre-demo checks. Follows docs/07_Demo_Script.md. Synthetic data only.
---

# SEHAT AI demo run

The demo runs **offline on one laptop** (Apple Silicon, 16 GB): local extraction model, deterministic rules engine,
scratch database. Follow `docs/07_Demo_Script.md` for the story. Synthetic patients only; never `sehat.db`.

## Before the day

- `make doctor` passes (Python, venv packages, Node, `frontend/node_modules`, model files, free ports).
- The local model is downloaded and hash-checked (from `backend/`):
  `"$PY" scripts/download_local_llm.py qwen3-4b-instruct-2507-q4km` (≈2.5 GB, Apache-2.0), then
  `"$PY" scripts/start_local_llm.py --check-only`.
- NeMo Guardrails is installed in the venv (`backend/requirements-guardrails.txt`); with `GUARDRAILS_ENABLED=1` and the
  package missing, the backend refuses to start (by design).
- Rehearse three times **with Wi-Fi off**. Record one full run as a backup video.

## Start (three terminals, from `backend/` unless noted)

`$PY` is the project venv's python. `S` is a scratch folder, for example `S="${TMPDIR:-/tmp}/sehat-demo"; mkdir -p "$S/docs"`.

1. Local model (foreground; Ctrl-C stops it): `"$PY" scripts/start_local_llm.py`
2. Backend:
   ```bash
   ENVIRONMENT=development DATABASE_PATH="$S/sehat-demo.db" OCR_DOCUMENT_DIR="$S/docs" \
     AI_PROVIDER=local GUARDRAILS_ENABLED=1 AI_KEYWORD_RED_FLAGS=1 AI_CLOUD_ENABLED=0 \
     "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8010
   ```
   The developer `.env` supplies the rest (facility mapping, OCR and voice switches); never print it.
3. Frontend (from `frontend/`): `NEXT_PUBLIC_API_URL=http://127.0.0.1:8010/api/v1 npx next dev -H 127.0.0.1 -p 3010`
4. Open `http://127.0.0.1:3010` and sign in with the demo accounts (`anm_demo`, `mo_demo`, `supervisor_demo`,
   `patient_demo`). They are shared, password-less, development-only accounts.

## Pre-demo check (5 minutes before)

- `"$PY" scripts/e2e_local_ai_check.py http://127.0.0.1:8010`: local model + guardrails walkthrough on synthetic text.
- `curl -s http://127.0.0.1:8010/api/v1/health` shows the providers you expect (extraction `local`, no cloud).
- Wi-Fi off. Notifications off. Screen recording ready. Backup video on the desktop.

## Say it honestly on stage

- "Research prototype, not a clinically validated device." "DPDP-ready by design", never "compliant".
- The rules engine sets urgency; the model only extracts; a named human signs off.
- Numbers come with denominators (for example red-flag candidates 15/15 on 20 synthetic cases with the keyword layer,
  0/15 from the model alone; `MODEL_CARD.md`).

## After

Stop the three processes you started (Ctrl-C, or `lsof -ti:8010 -sTCP:LISTEN | xargs kill`, same for 3010) and delete `$S`.
A seeded synthetic demo patient and a one-command demo target come in Phase 7 (`RUNBOOK.md`).
