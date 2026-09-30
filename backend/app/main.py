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
from app.errors import register_error_handlers
from app.routes import auth, health, triage
from app.services.kernel import build_kernel

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    await init_db(settings.database_path)
    app.state.kernel = build_kernel(settings)
    yield


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
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    register_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(triage.router, prefix=API_PREFIX)
    return app


app = create_app()
