"""SEHAT AI backend entry point.

Run from `backend/`:  uvicorn app.main:app --reload
Research prototype, not a clinically validated device.
"""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.errors import SafeErrorMiddleware, register_error_handlers
from app.routes import ai, audit, auth, cases, health, ocr, review, triage, voice
from app.services.kernel import build_kernel

API_PREFIX = "/api/v1"

# Third-party loggers that could emit input text at DEBUG/INFO (docs/11 §G).
# aiosqlite logs every SQL statement WITH its parameters at DEBUG (case data, transcripts, OCR values);
# found by the Phase 5 log-canary test. Kept at WARNING whatever LOG_LEVEL is.
_QUIET_LOGGERS = ("presidio-analyzer", "presidio-anonymizer", "spacy", "semantic_kernel", "aiosqlite", "RapidOCR", "rapidocr", "httpx", "python_multipart", "PIL")


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
    from app.ai.providers import build_provider

    app.state.ai_provider = build_provider(settings)  # None when AI_PROVIDER=none (AI endpoints answer 503)
    app.state.translator = None
    if settings.translation_enabled:
        from app.ai.translate import build_translator

        app.state.translator = build_translator(settings)
    yield
    from app.ocr.worker_client import shutdown_all

    shutdown_all()  # the local OCR worker (and its llama-server) never outlives the API


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SEHAT AI", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = _safe_request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
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
    return app


app = create_app()
