# SEHAT AI: shortcuts for setup, checks, tests, evaluations and local runs (RUNBOOK.md). GNU Make 3.81 or later.
# Every target calls an existing script or tool. Nothing here installs globally, reads .env or touches sehat.db.
#
#   make help            list the targets
#   make doctor          read-only environment check
#   make test            backend suite + frontend tests, types and lint
#
# Python: .venv/bin/python here, else the main checkout's .venv (so worktrees work), else python3.
# Override with PY=/path/to/.venv/bin/python.

GIT_COMMON := $(shell git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
MAIN_ROOT := $(patsubst %/.git,%,$(GIT_COMMON))
PY ?= $(firstword $(wildcard .venv/bin/python $(MAIN_ROOT)/.venv/bin/python) python3)
PYTHON := $(if $(wildcard $(PY)),$(abspath $(PY)),$(PY))

HOST ?= 127.0.0.1
API_PORT ?= 8010
WEB_PORT ?= 3010
SCRATCH ?= $(or $(TMPDIR),/tmp)/sehat-dev
JUNIT ?= $(or $(TMPDIR),/tmp)/sehat-junit.xml
LLM ?= qwen3-4b-instruct-2507-q4km

TEST_ENV := SEHAT_DOTENV=0 ENVIRONMENT=test
PYTEST := $(TEST_ENV) "$(PYTHON)" -m pytest -q -p no:cacheprovider
SAFETY_TESTS := tests/rules tests/privacy/test_urgency_authority.py tests/privacy/test_boundary.py \
	tests/privacy/test_gateway.py tests/privacy/test_consent.py tests/privacy/test_authz.py tests/privacy/test_audit.py \
	tests/privacy/test_config_hardening.py tests/ai/test_pure.py tests/ai/test_redflag_keywords.py \
	tests/ai/test_encounter_eval.py tests/ocr/test_safety_boundary.py tests/review/test_red_lifecycle.py \
	tests/review/test_facility.py tests/ci

.DEFAULT_GOAL := help
.PHONY: help doctor _venv setup setup-backend setup-guardrails setup-voice setup-frontend test test-backend \
	test-frontend ci hooks-test safety-test safety-eval safety-eval-quick eval-summary llm-list llm-download llm-check \
	llm-start dev-backend dev-frontend demo demo-seed demo-check

SAFETY_OUT ?= $(or $(TMPDIR),/tmp)/sehat-safety-eval

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-17s %s\n", $$1, $$2}'
	@echo ""
	@echo "  Python: $(PYTHON)   (override: make <target> PY=/path/to/.venv/bin/python)"

doctor: ## Read-only environment check: Python, venv, packages, Node, model files, ports
	"$(PYTHON)" backend/scripts/doctor.py

_venv:
	@"$(PYTHON)" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)' || \
	  { echo "$(PYTHON) is not a virtual environment. Create one first: python3.11 -m venv .venv"; exit 1; }

setup: setup-backend setup-frontend ## Backend core + test packages into the venv, then frontend packages

setup-backend: _venv ## pip install backend core + dev requirements into the venv
	"$(PYTHON)" -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt

setup-guardrails: _venv ## Optional: NeMo Guardrails (needed when GUARDRAILS_ENABLED=1)
	"$(PYTHON)" -m pip install -r backend/requirements-guardrails.txt

setup-voice: _venv ## Optional: Silero VAD and the local speech stack (docs/12)
	"$(PYTHON)" -m pip install -r backend/requirements-voice.txt

setup-frontend: ## npm ci in frontend/ (local node_modules only)
	cd frontend && npm ci

test: test-backend test-frontend ## Backend suite, then frontend tests, TypeScript and ESLint

test-backend: ## Full backend suite, isolated from any .env
	cd backend && $(PYTEST)

test-frontend: ## Frontend unit tests, TypeScript, ESLint
	cd frontend && npm test && npx tsc --noEmit && npx eslint .

ci: ## What CI runs: migration checks, full suite with JUnit, skip gate, frontend
	cd backend && $(PYTEST) tests/privacy/test_migration*.py tests/voice/test_schema_consent.py
	cd backend && $(PYTEST) -rs --junitxml="$(JUNIT)"
	cd backend && "$(PYTHON)" scripts/ci_skip_gate.py "$(JUNIT)"
	$(MAKE) test-frontend

hooks-test: ## Agent harness tests (HARNESS.md)
	cd backend && $(PYTEST) tests/ci/test_agent_harness.py

safety-test: ## Safety-critical modules only: rules, boundaries, consent, guard, audit, RED, CI gates
	cd backend && $(PYTEST) $(SAFETY_TESTS)

safety-eval: ## Safety scorecard: rules, guards, redaction + safety suites + evaluations (docs/25; about 30 s)
	cd backend && $(TEST_ENV) "$(PYTHON)" -m safety_eval --out "$(SAFETY_OUT)"

safety-eval-quick: ## Safety scorecard without the pytest suites (a few seconds)
	cd backend && $(TEST_ENV) "$(PYTHON)" -m safety_eval --quick --out "$(SAFETY_OUT)"

eval-summary: ## Encounter-summary evaluation on 47 synthetic cases (offline; prints denominators)
	cd backend && $(TEST_ENV) "$(PYTHON)" scripts/eval_encounter_summary.py

llm-list: ## List the pinned local models and their measurement status
	cd backend && "$(PYTHON)" scripts/download_local_llm.py --list

llm-download: ## Download the pinned local model (LLM=...; default Qwen3-4B, about 2.5 GB)
	cd backend && "$(PYTHON)" scripts/download_local_llm.py $(LLM)

llm-check: ## Check the local model file hash and the llama.cpp build, then exit
	cd backend && "$(PYTHON)" scripts/start_local_llm.py --check-only

llm-start: ## Start the loopback-only local model server (foreground; Ctrl-C stops it)
	cd backend && "$(PYTHON)" scripts/start_local_llm.py

dev-backend: ## Backend on HOST:API_PORT (127.0.0.1:8010) with a scratch database; .env supplies the rest
	mkdir -p "$(SCRATCH)/docs"
	cd backend && ENVIRONMENT=development DATABASE_PATH="$(SCRATCH)/sehat-dev.db" OCR_DOCUMENT_DIR="$(SCRATCH)/docs" \
	  CORS_ALLOWED_ORIGINS="http://$(HOST):$(WEB_PORT)" "$(PYTHON)" -m uvicorn app.main:app --host $(HOST) --port $(API_PORT)

demo: ## Offline demo: local model + backend + frontend on a fresh scratch database, seeded and checked (docs/07; Ctrl-C stops)
	cd backend && "$(PYTHON)" -m demo_kit run --api-port $(API_PORT) --web-port $(WEB_PORT) $(if $(ONLINE),--online) \
	  $(if $(filter fake,$(DEMO_MODEL)),--provider fake) $(if $(NO_LLM),--no-llm)

demo-seed: ## Seed a running backend (API_PORT) with the synthetic demo cases; refuses a non-empty database
	cd backend && "$(PYTHON)" -m demo_kit seed --api http://$(HOST):$(API_PORT)

demo-check: ## Pre-demo check of a running stack: model, guardrails, rules beats, seeded queue, frontend
	cd backend && "$(PYTHON)" -m demo_kit check --api http://$(HOST):$(API_PORT) --web http://$(HOST):$(WEB_PORT) \
	  $(if $(filter fake,$(DEMO_MODEL)),--provider fake)

dev-frontend: ## Frontend dev server on HOST:WEB_PORT (127.0.0.1:3010), talking to dev-backend
	cd frontend && NEXT_PUBLIC_API_URL="http://$(HOST):$(API_PORT)/api/v1" npx next dev -H $(HOST) -p $(WEB_PORT)
