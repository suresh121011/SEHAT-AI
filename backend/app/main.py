"""SEHAT AI backend entry point.

Run from `backend/`:  uvicorn app.main:app --reload
Research prototype, not a clinically validated device.
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.errors import SafeErrorMiddleware, register_error_handlers
from app import security_events
from app.routes import ai, audit, auth, cases, evidence, health, interview, lab_timeline, ocr, patient_identity, patients, referral_notes, review, triage, voice
from app.services.kernel import build_kernel

API_PREFIX = "/api/v1"

# Third-party loggers that could emit input text at DEBUG/INFO (docs/11 §G).
# aiosqlite logs every SQL statement WITH its parameters at DEBUG (case data, transcripts, OCR values);
# found by the Phase 5 log-canary test. Kept at WARNING whatever LOG_LEVEL is.
# nemoguardrails logs every rail event WITH the (redacted) case text at INFO; found by the local-AI walkthrough (docs/16 §2b).
_QUIET_LOGGERS = ("presidio-analyzer", "presidio-anonymizer", "spacy", "semantic_kernel", "aiosqlite", "RapidOCR", "rapidocr", "httpx", "python_multipart", "PIL",
                  "nemoguardrails")


def _quiet_third_party_loggers() -> None:
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _safe_request_id(value: str | None) -> str:
    """Accept a client X-Request-ID only if it is a UUID; otherwise generate one (no client text in logs/audit)."""
    if value:
        try:
            return str(uuid.UUID(value))
        except ValueError:
            pass
    return str(uuid.uuid4())


async def _warm_up(warm) -> None:
    status = await warm()
    logging.getLogger("sehat.ai").info("local model warm-up: %s", status)  # reason code only


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    _quiet_third_party_loggers()
    await init_db(settings.database_path)
    if settings.ocr_enabled:
        from app.database import _connect
        from app.ocr.service import startup_sweep

        conn = await _connect(settings.database_path)
        try:
            await startup_sweep(conn, settings)  # abandoned pending rows; orphaned page files
        finally:
            await conn.close()
    app.state.kernel = build_kernel(settings)
    from app.ai.azure_chat import initial_azure_status
    from app.services.kernel import probe_azure

    # Azure: one fixed synthetic prompt in the background, so /health says whether the connection works (a wrong
    # deployment name used to show as "configured"). Startup is never blocked and a failure is not fatal.
    app.state.azure_chat = initial_azure_status(settings)
    app.state.azure_check = None
    if app.state.azure_chat["state"] == "not_checked":
        async def _azure_check():
            app.state.azure_chat = await probe_azure(app.state.kernel)

        app.state.azure_check = asyncio.create_task(_azure_check())
    from app.ai.providers import build_provider

    app.state.ai_provider = build_provider(settings)  # None when AI_PROVIDER=none (AI endpoints answer 503)
    warm = getattr(app.state.ai_provider, "warm_up", None)
    # Local model: warm up in the background (synthetic text only); startup is not blocked and a failure is not fatal.
    app.state.ai_warm_up = asyncio.create_task(_warm_up(warm)) if warm is not None else None
    from app.ai.guardrails import build_guardrails

    app.state.guardrails = build_guardrails(settings)  # None unless GUARDRAILS_ENABLED=1; refuses to start if not installed
    if app.state.guardrails is not None:
        await app.state.guardrails.warm_up()
    app.state.translator = None
    if settings.translation_enabled:
        from app.ai.translate import build_translator

        app.state.translator = build_translator(settings)
    yield
    if app.state.ai_warm_up is not None and not app.state.ai_warm_up.done():
        app.state.ai_warm_up.cancel()
    if app.state.azure_check is not None and not app.state.azure_check.done():
        app.state.azure_check.cancel()
    from app.ocr.worker_client import shutdown_all

    shutdown_all()  # the local OCR worker (and its llama-server) never outlives the API


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SEHAT AI", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,  # S-4: bearer tokens only; the browser uses the same-origin Next.js proxy
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = _safe_request_id(request.headers.get("X-Request-ID"))
        events = security_events.start()
        response = await call_next(request)
        # Denials noted during the request are written now, after its transaction ended (A5). A failure here must
        # not change the response the client already earned; it is logged without detail.
        try:
            await security_events.flush(events, request.state.request_id)
        except Exception:  # noqa: BLE001
            logging.getLogger("sehat.security").warning("security_event_write_failed count=%d", len(events))
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    # Added last = outermost user middleware: catches anything the inner layers re-raise.
    app.add_middleware(SafeErrorMiddleware)

    register_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    # Before triage/audit: its static paths (/triage/queue, /audit/governance) must win over /audit/{case_id}.
    app.include_router(review.router, prefix=API_PREFIX)
    app.include_router(triage.router, prefix=API_PREFIX)
    app.include_router(cases.router, prefix=API_PREFIX)
    app.include_router(audit.router, prefix=API_PREFIX)
    app.include_router(voice.router, prefix=API_PREFIX)
    app.include_router(ocr.router, prefix=API_PREFIX)
    app.include_router(ai.router, prefix=API_PREFIX)
    app.include_router(patients.router, prefix=API_PREFIX)  # P1 patient registry
    app.include_router(patient_identity.router, prefix=API_PREFIX)  # patient login ↔ patient record binding (hardening A3)
    app.include_router(interview.router, prefix=API_PREFIX)  # scripted voice interviewer (docs/plans/scripted-voice-interviewer.md)
    app.include_router(referral_notes.router, prefix=API_PREFIX)  # referral closure tracking (docs/21)
    app.include_router(lab_timeline.router, prefix=API_PREFIX)  # lab timeline across reports and linked visits (docs/22)
    app.include_router(evidence.router, prefix=API_PREFIX)  # protocol evidence behind fired rules (docs/23)
    return app


app = create_app()
