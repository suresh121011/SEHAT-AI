"""Voice intake routes (docs/12 §7). Thin wrappers over app.voice.service.

Audio is uploaded as a raw `audio/wav` request body (not multipart: Starlette spools multipart file
parts over 1 MB to a temporary file on disk). The body is read in chunks with a hard byte cap and the
request is aborted as soon as the cap is exceeded, so audio stays in process memory and is bounded.
"""

import importlib.util
import uuid
from typing import Any, Literal

import aiosqlite
import asyncio
import base64
import json
import logging
from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.auth import Principal, Role, decode_token, get_current_principal, require_roles
from app.config import Settings, get_settings
from app.database import get_db
from app.errors import ApiError
from app.voice import engines, gemini_live, gemini_live_service, service
from app.voice.audio import max_bytes, validate_wav

router = APIRouter(tags=["voice"])

logger = logging.getLogger("sehat.routes.voice")

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
        "gemini_live": {
            "enabled": settings.gemini_live_enabled,
            "ready": settings.gemini_live_enabled and settings.gemini_live_configured,
            "model": settings.gemini_live_model,
            "languages": {"hi": True, "or": True},
        },
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


@router.websocket("/cases/{case_id}/voice/live-followup")
async def live_voice_followup(
    websocket: WebSocket,
    case_id: uuid.UUID,
    language: Literal["hi", "or"] = "hi",
    token: str | None = Query(default=None),
) -> None:
    """Duplex voice follow-up powered by Gemini 2.0 Flash Multimodal Live (docs/20).

    Validates bearer token and voice_cloud consent, streams 16kHz PCM audio bidirectionally,
    and returns real-time tool events as missing fields are spoken by the patient.
    """
    await websocket.accept()
    settings = get_settings()

    # 1. Authenticate token
    effective_token = token or websocket.cookies.get("sehat_session")
    if not effective_token:
        await websocket.close(code=4401, reason="UNAUTHORIZED")
        return

    try:
        principal = decode_token(effective_token)
    except Exception:
        await websocket.close(code=4401, reason="UNAUTHORIZED")
        return

    if principal.role not in (Role.PATIENT, Role.ANM, Role.MEDICAL_OFFICER):
        await websocket.close(code=4403, reason="FORBIDDEN")
        return

    # 2. Check feature enabled
    if not settings.voice_enabled or not settings.gemini_live_enabled:
        await websocket.close(code=4404, reason="FEATURE_DISABLED")
        return

    from app import consent
    from app.rules.required_fields import missing

    case_id_str = str(case_id)
    # 3. Check case access & consent
    try:
        async with aiosqlite.connect(settings.database_path) as db:
            db.row_factory = aiosqlite.Row
            case_row = await consent.load_case(db, principal, case_id_str, "write")
            snap = await consent.snapshot(db, case_id_str)
            if not snap.is_effective("triage") or not snap.is_effective("voice_cloud"):
                await websocket.close(code=4403, reason="CONSENT_REQUIRED")
                return
            scenario = case_row["scenario"]
    except ApiError:
        await websocket.close(code=4404, reason="CASE_NOT_FOUND")
        return
    except Exception as e:
        logger.exception("Error checking case access for live voice: %s", e)
        await websocket.close(code=4500, reason="INTERNAL_ERROR")
        return

    # 4. Resolve missing fields for scenario
    gaps = missing(scenario, set())
    missing_fields = [{"field_name": r.key, "label": r.label, "is_danger_sign": r.danger_sign} for r in gaps]

    session_id: str | None = None
    try:
        async with aiosqlite.connect(settings.database_path) as db_sess:
            db_sess.row_factory = aiosqlite.Row
            session_id = await gemini_live_service.create_live_session(
                db_sess,
                case_id=case_id_str,
                principal=principal,
                language=language,
                consent_seq=snap.authz_seq,
            )
    except Exception as e:
        logger.exception("Failed to initialize gemini live session row: %s", e)

    test_transport = getattr(websocket.app.state, "gemini_live_transport", None)
    session = gemini_live.GeminiLiveSession(
        api_key=settings.google_ai_api_key,
        model=settings.gemini_live_model,
        test_transport=test_transport,
    )

    try:
        await session.connect(language=language, missing_fields=missing_fields)
    except Exception as exc:
        logger.exception("Gemini Live connection failed: %s", exc)
        await websocket.close(code=4502, reason="GEMINI_LIVE_UNAVAILABLE")
        return

    async def forward_client_to_gemini():
        try:
            while True:
                msg = await websocket.receive()
                if "bytes" in msg and msg["bytes"]:
                    await session.send_audio(msg["bytes"])
                elif "text" in msg and msg["text"]:
                    try:
                        parsed = json.loads(msg["text"])
                        if parsed.get("type") == "audio" and "data" in parsed:
                            pcm = base64.b64decode(parsed["data"])
                            await session.send_audio(pcm)
                        elif parsed.get("type") == "stop":
                            break
                    except Exception:
                        pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("Client websocket ended: %s", e)

    async def forward_gemini_to_client():
        try:
            async for ev in session.receive_events():
                if ev.event_type == "audio" and ev.audio_data:
                    await websocket.send_json({
                        "event": "audio",
                        "data": base64.b64encode(ev.audio_data).decode("ascii"),
                    })
                elif ev.event_type == "tool_call":
                    if ev.tool_name == "submit_missing_field":
                        raw_field_name = ev.tool_args.get("field_name")
                        field_name = gemini_live.normalize_field_name(raw_field_name)
                        val = ev.tool_args.get("value")
                        unit = ev.tool_args.get("unit")
                        phrase = ev.tool_args.get("patient_phrase")
                        field_id = str(uuid.uuid4())
                        if session_id:
                            try:
                                async with aiosqlite.connect(settings.database_path) as db_field:
                                    db_field.row_factory = aiosqlite.Row
                                    rec = await gemini_live_service.record_live_field(
                                        db_field,
                                        session_id=session_id,
                                        case_id=case_id_str,
                                        principal=principal,
                                        field_name=str(field_name),
                                        value=str(val),
                                        unit=str(unit) if unit else None,
                                        patient_phrase=str(phrase) if phrase else None,
                                    )
                                    field_id = rec["field_id"]
                            except Exception as e:
                                logger.exception("Failed to persist live field: %s", e)

                        await websocket.send_json({
                            "event": "field_submitted",
                            "field_id": field_id,
                            "field_name": field_name,
                            "value": val,
                            "unit": unit,
                            "patient_phrase": phrase,
                            "status": "pending_review",
                            "source": "gemini_live_duplex",
                        })
                        if ev.tool_call_id:
                            await session.send_tool_result(ev.tool_call_id, ev.tool_name, {"status": "recorded", "field": field_name})
                    elif ev.tool_name == "finish_intake":
                        reason = ev.tool_args.get("reason", "all_fields_collected")
                        await websocket.send_json({
                            "event": "intake_finished",
                            "reason": reason,
                        })
                        if session_id:
                            try:
                                async with aiosqlite.connect(settings.database_path) as db_fin:
                                    await gemini_live_service.complete_live_session(db_fin, session_id=session_id, reason=reason)
                            except Exception:
                                pass
                        if ev.tool_call_id:
                            await session.send_tool_result(ev.tool_call_id, ev.tool_name, {"status": "ok"})
                        break
                elif ev.event_type == "text" and ev.text:
                    _, safe_text = gemini_live.filter_non_diagnostic_speech(ev.text, language=language)
                    await websocket.send_json({"event": "transcript", "text": safe_text})
                elif ev.event_type == "turn_complete":
                    await websocket.send_json({"event": "turn_complete"})
                elif ev.event_type == "error":
                    await websocket.send_json({"event": "error", "message": ev.error_message})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("Gemini stream ended: %s", e)

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(forward_client_to_gemini()),
                asyncio.create_task(forward_gemini_to_client()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        if session_id:
            try:
                async with aiosqlite.connect(settings.database_path) as db_fin:
                    await gemini_live_service.complete_live_session(db_fin, session_id=session_id, reason="disconnected")
            except Exception:
                pass
        await session.close()
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/cases/{case_id}/voice/live-fields")
async def list_live_fields(
    case_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    _: Settings = Depends(_voice_enabled),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Lists all fields extracted via Gemini Live Duplex voice follow-up for a case."""
    from app import consent

    await consent.load_case(db, principal, str(case_id), "read")
    fields = await gemini_live_service.list_live_fields(db, str(case_id))
    return {"case_id": str(case_id), "fields": fields}
