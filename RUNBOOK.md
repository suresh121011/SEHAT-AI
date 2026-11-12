# SEHAT AI runbook

How to set up, run, test and demo SEHAT AI. `make help` lists every shortcut; each one calls an existing script.
Synthetic data only. Never use `sehat.db` for checks, never commit `.env`.

## 1. Prerequisites

| Tool | Version | Notes |
|:---|:---|:---|
| Python | 3.11 (CI uses 3.11) | NeMo Guardrails needs Python below 3.14 |
| Node.js | 20 or later (CI uses 24) | |
| Git | 2.31 or later | The Makefile finds the main checkout's venv from a worktree |
| GNU Make | 3.81 or later | Preinstalled on macOS |
| llama.cpp (`llama-server`) | Homebrew llama.cpp 0.5.0, build 11146 tested | Only for the local model (`AI_PROVIDER=local`) |
| Hardware | Apple Silicon, 16 GB tested | The local model server uses about 5 GB |
| Disk | About 3 GB for the default local model; more for OCR, voice and MedGemma | |

## 2. First-time setup

```bash
git clone https://github.com/suresh121011/SEHAT-AI.git sehat-ai
```

```bash
cd sehat-ai && python3.11 -m venv .venv
```

```bash
make setup
```

```bash
make doctor
```

`make setup` installs the backend core and test packages into `.venv` and runs `npm ci` in `frontend/`; it refuses
to run outside a virtual environment. Then create `.env` yourself: copy `.env.example` to `.env` and fill in your own
values. Never commit it. (Coding agents are blocked from reading or writing it.)

Optional stacks:

| Stack | How | Docs |
|:---|:---|:---|
| NeMo Guardrails (recommended for demos) | `make setup-guardrails` | `docs/16` §2b |
| Voice (Silero VAD, local speech) | `make setup-voice`, then `backend/scripts/download_voice_models.py` | `docs/12` |
| OCR engines (separate worker venv `.venv-ocr`) | Follow `docs/14` §9 | `docs/14` |
| Local translation (IndicTrans2, gated) | `backend/requirements-translation.txt`, then `download_translation_models.py` | `.env.example` |
| MedGemma 1.5 (gated, about 8.6 GB) | `backend/scripts/download_medgemma_local.py` after accepting the model terms | `docs/18` §4a |

## 3. Run the app

Two terminals:

```bash
make dev-backend
```

```bash
make dev-frontend
```

Open `http://127.0.0.1:3010` and sign in with a demo account: `anm_demo`, `mo_demo`, `supervisor_demo` or
`patient_demo` (shared, password-less, development only). The backend uses a scratch database under
`$TMPDIR/sehat-dev`; your `.env` supplies everything else (facility mapping, providers, switches).

- The defaults are `127.0.0.1:8010` and `127.0.0.1:3010`, so they never collide with servers on 8000 and 3000. To use
  other ports: `make dev-backend API_PORT=8000`, and `make dev-frontend WEB_PORT=3000 API_PORT=8000`.
- Without `ACCOUNT_FACILITIES` in `.env` the backend refuses to start. To run without facility isolation, knowingly:
  `ALLOW_UNSCOPED_DEMO=true make dev-backend`.
- Use the dev server, not `next start`: the production session cookie is Secure and will not be set over plain HTTP.

## 4. Local AI model (offline extraction)

```bash
make llm-download
```

```bash
make llm-check
```

```bash
make llm-start
```

`llm-start` runs in the foreground (Ctrl-C stops it). Then set `AI_PROVIDER=local` in your `.env` and start the
backend. To check the whole local path on synthetic text, from `backend/`:
`<venv>/bin/python scripts/e2e_local_ai_check.py http://127.0.0.1:8010`.

## 5. Tests and CI

| Command | What it runs |
|:---|:---|
| `make test` | Backend suite, then frontend tests, TypeScript and ESLint |
| `make ci` | What CI runs: migration checks, full suite with JUnit, the skip gate, then the frontend |
| `make safety-test` | Only the safety-critical modules (rules, boundaries, consent, guard, audit, RED lifecycle, CI gates) |
| `make hooks-test` | The agent harness tests |

From a worktree, the Makefile uses the main checkout's `.venv` automatically; override with `PY=/path/to/python`.
CI runs on every pull request to `main`: backend tests and the skip gate, frontend checks, and a gitleaks secret scan.
Branch protection needs the checks to pass and one approving review.

## 6. Evaluations

| Command | What it measures |
|:---|:---|
| `make eval-summary` | The encounter summary on 47 synthetic cases, offline; prints every metric with its denominator |
| `<venv>/bin/python scripts/measure_local_llm.py` (from `backend/`, model server running) | The local model gate on 20 synthetic cases: validity, grounding, values, red flags, latency |
| `backend/scripts/e2e_*.py` | End-to-end checks of the intake, review, OCR proxy, summary note and real app (each file's docstring has its commands) |

## 7. Demo

Follow `docs/07_Demo_Script.md` and the `sehat-demo-run` skill (`.claude/skills/sehat-demo-run/SKILL.md`): pre-day
checks, the three-terminal start, the pre-demo check, and what to say honestly. A seeded synthetic demo patient and a
one-command demo target arrive in Phase 7.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|:---|:---|:---|
| `facility isolation is off: set ACCOUNT_FACILITIES …` | No account-to-facility mapping | Set it in `.env` (see `.env.example`), or `ALLOW_UNSCOPED_DEMO=true` knowingly |
| `password-less demo authentication cannot run outside development/test` | `ENVIRONMENT` is not `development` or `test` | Use `development` locally |
| `JWT_SECRET_KEY must be at least 32 bytes` | Short secret in `.env` | Run `backend/scripts/rotate_jwt_secret.py` yourself (it never prints the key) |
| `AI_PROVIDER=azure requires …` | Azure checklist incomplete | Follow the Azure checklist in `.env.example`; synthetic data only |
| Server refuses to start with `GUARDRAILS_ENABLED=1` | NeMo Guardrails not installed | `make setup-guardrails`, or set `GUARDRAILS_ENABLED=0` |
| `LOCAL_LLM_URL must be http://127.0.0.1:<port>` | Non-loopback model URL | Loopback only, by design |
| `OCR_RETENTION_DAYS must be set when OCR_ENABLED=1` | Retention not chosen | A number of days, or `none` |
| Address already in use | Another server on the port | `make doctor` shows busy ports; pick other ports. Never stop a server you did not start |
| Sign-in does not stick | Frontend started with `next start` over HTTP | Use `make dev-frontend` |
| Voice requests answer 404 | `VOICE_ENABLED=0` | Expected; enable voice in `.env` if needed |
| Local test counts differ from CI | NeMo Guardrails tests run locally, CI skips them (allow-listed) | Compare with the baseline below |
| Claude Code refuses or asks before a command | A project rule in the agent harness | See `HARNESS.md`; change the approach or ask the owner |

## 9. Test baseline

Every change must keep these numbers or raise them. Record a new row when a phase lands.

| Date | Commit | Backend (local) | Backend (CI) | Frontend |
|:---|:---|:---|:---|:---|
| 2026-11-12 | `main` at `ba9bee5` | 1658 passed, 14 skipped, 5 xfailed | 1613 executed of 1633; skip gate ok (minimum 1604, 27 required modules) | 143 passed; TypeScript clean |
| 2026-11-12 | `main` at `9132951` (Phase 1 merged) | 1787 passed, 14 skipped, 5 xfailed | 1742 executed; skip gate ok (minimum 1733, 28 required modules) | 143 passed; TypeScript and ESLint clean |
| 2026-11-13 | `feat/referral-notes` (Phase 2) | 1828 passed, 14 skipped, 5 xfailed | 1783 expected; minimum 1774, 31 required modules | 150 passed; TypeScript and ESLint clean |

The 14 local skips are opt-in live tests; the 5 expected failures document known PII-scrubber misses.

## 10. Roadmap

| Phase | Work | Status |
|:---|:---|:---|
| 1 | Governance and agent harness | Merged (PR #24) |
| 2 | Referral closure tracking: signed-off referral packet, append-only status to closure, 48-hour overdue flag, printable letter, FHIR R4 export (features #26–29; `docs/21`) | In review |
| 3 | Lab timeline across reports with missing, stale and changed flags; no urgency output | Planned |
| 4 | Evidence-locked protocol retrieval: the checked source passage behind each fired rule, abstaining when none is checked (feature #40) | Planned |
| 5 | Semantic Kernel assistant screen with closed actions and a quote gate (steps 3–7 of `docs/plans/semantic-kernel-orchestration-plan.md`) | Planned |
| 6 | `make safety-eval`: one safety scorecard with denominators (feature #31) | Planned |
| 7 | Demo hardening: seeded synthetic patient, offline rehearsals, backup recording | Planned |
