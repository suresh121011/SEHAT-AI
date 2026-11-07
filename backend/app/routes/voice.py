"""Voice intake routes (docs/12 §7). Thin wrappers over app.voice.service.

Audio is uploaded as a raw `audio/wav` request body (not multipart: Starlette spools multipart file
parts over 1 MB to a temporary file on disk). The body is read in chunks with a hard byte cap and the
request is aborted as soon as the cap is exceeded, so audio stays in process memory and is bounded.
"""

import importlib.util
import uuid
from typing import Any, Literal

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.auth import Principal, Role, get_current_principal, require_roles
from app.config import Settings, get_settings
from app.database import get_db
from app.errors import ApiError
from app.voice import engines, service
from app.voice.audio import max_bytes, validate_wav

router = APIRouter(tags=["voice"])

_recorders = require_roles(Role.PATIENT, Role.ANM)
_reviewers = require_roles(Role.ANM, Role.MEDICAL_OFFICER)

# What has actually been exercised, per engine and language (docs/12 §9). Updated from test runs, never
# inferred from a flag: `tested_real` = a real engine produced this language; `tested_mock` = contract
# tests only; `unsupported` = the engine does not cover the language.
VERIFICATION: dict[str, dict[str, str]] = {
    "local": {"en": "unsupported", "hi": "tested_real", "or": "tested_real"},  # hi: synthetic + live mic; or: 2 live-mic clips only (docs/12 §9.2)
    "cloud": {"en": "tested_mock", "hi": "tested_mock", "or": "tested_mock"},
}


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _voice_enabled(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.voice_enabled:
        raise ApiError(404, "FEATURE_DISABLED", "Voice input is not enabled on this server")
    return settings


@router.get("/voice/capabilities")
async def capabilities(_: Principal = Depends(get_current_principal), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Which voice components are enabled and usable. No secrets; no model is loaded to answer this."""
    local_ready = settings.voice_local_asr_enabled and engines.local_model_installed(settings.voice_local_model_dir)
    return {
        "voice_enabled": settings.voice_enabled,
        "vad": {"enabled": settings.voice_vad_enabled, "installed": importlib.util.find_spec("silero_vad") is not None},
        "engines": {
            "local": {
                "enabled": settings.voice_local_asr_enabled,
                "ready": local_ready,
                "label": "On this device (local model)",
                "model": engines.LOCAL_MODEL_ID,
                "languages": {lang: engines.local_supports(lang) for lang in ("en", "hi", "or")},  # type: ignore[arg-type]
            },
            "cloud": {
                "enabled": settings.voice_cloud_stt_enabled,
                "ready": settings.voice_cloud_stt_enabled and settings.sarvam_configured,
                "label": "Internet — Sarvam AI (needs separate consent)",
                "model": engines.SARVAM_STT_MODEL,
                "languages": {"en": True, "hi": True, "or": True},
            },
        },
        "tts": {"enabled": settings.voice_tts_enabled, "ready": settings.voice_tts_enabled and settings.sarvam_configured},
        "verification": VERIFICATION,
        "max_seconds": settings.voice_max_seconds,
    }


async def _read_capped(request: Request, cap: int) -> bytes:
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype not in ("audio/wav", "audio/x-wav", "audio/wave"):
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Send the recording as audio/wav")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > cap:
        raise ApiError(413, "AUDIO_TOO_LARGE", "The recording is too long")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise ApiError(413, "AUDIO_TOO_LARGE", "The recording is too long")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/cases/{case_id}/voice/transcriptions",
    openapi_extra={"requestBody": {"required": True, "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def create_transcription(
    case_id: uuid.UUID,
    request: Request,
    language: Literal["en", "hi", "or"] = Query(...),
    engine: Literal["local", "cloud"] = Query(...),
    idempotency_key: uuid.UUID = Query(...),
    principal: Principal = Depends(_recorders),
    settings: Settings = Depends(_voice_enabled),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict[str, Any]:
    service.check_engine(settings, engine, language)  # fail fast before reading audio
    body = await _read_capped(request, max_bytes(settings.voice_max_seconds))
    clip = validate_wav(body, settings.voice_max_seconds)
    del body
    transport = getattr(request.app.state, "voice_cloud_transport", None)  # tests only; None in production
    return await service.transcribe(db, principal, str(case_id), clip, language, engine, str(idempotency_key), settings, _rid(request), cloud_transport=transport)


@router.get("/cases/{case_id}/voice/transcriptions")
async def list_transcriptions(case_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal), _: Settings = Depends(_voice_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return {"case_id": str(case_id), "transcriptions": await service.list_transcriptions(db, principal, str(case_id), _rid(request))}


@router.get("/cases/{case_id}/voice/transcriptions/{transcription_id}")
async def get_transcription(case_id: uuid.UUID, transcription_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal), _: Settings = Depends(_voice_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.get_transcription(db, principal, str(case_id), str(transcription_id), _rid(request))


@router.post("/cases/{case_id}/voice/candidates/{candidate_id}/readback")
async def readback(case_id: uuid.UUID, candidate_id: uuid.UUID, body: service.ReadbackBody, request: Request, principal: Principal = Depends(_reviewers), _: Settings = Depends(_voice_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.resolve_readback(db, principal, str(case_id), str(candidate_id), body, _rid(request))


@router.post("/cases/{case_id}/voice/candidates/{candidate_id}/tts", response_class=Response)
async def readback_audio(case_id: uuid.UUID, candidate_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers), settings: Settings = Depends(_voice_enabled), db: aiosqlite.Connection = Depends(get_db)) -> Response:
    from app.voice import tts

    audio = await tts.readback_audio(db, principal, str(case_id), str(candidate_id), settings, _rid(request), transport=getattr(request.app.state, "voice_cloud_transport", None))
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.get("/cases/{case_id}/voice/prefill")
async def prefill(case_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers), _: Settings = Depends(_voice_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.prefill(db, principal, str(case_id), _rid(request))
