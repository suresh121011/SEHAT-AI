"""Document OCR routes (docs/14 §6; docs/06 `POST /intake/document`). Thin wrappers over app.ocr.service.

The upload is multipart (docs/06), but it is parsed in memory with python-multipart's streaming parser
instead of Starlette's form parser, which spools parts over 1 MB to a temporary file on disk. The body is
read in chunks with a hard byte cap, so the document stays in process memory and is bounded. Exactly one
file part and the fixed fields case_id (UUID), document_type (enum) and idempotency_key (UUID) are accepted.
"""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from python_multipart.multipart import MultipartParser, parse_options_header

from app.auth import Principal, Role, get_current_principal, require_roles
from app.config import Settings, get_settings
from app.database import get_db
from app.errors import ApiError
from app.ocr import engine as paddle
from app.ocr import service

router = APIRouter(tags=["documents"])

_uploaders = require_roles(Role.ANM)  # Phase 5: ANM only (docs/14 §4A: a patient upload could not be reviewed)
_reviewers = require_roles(Role.ANM, Role.MEDICAL_OFFICER)
_DOC_TYPES = ("lab_report", "prescription", "discharge_summary")
_FORM_OVERHEAD = 16 * 1024
# Uploads buffered in memory at once (each up to OCR_MAX_BYTES). Checked BEFORE the body is read, so many
# concurrent uploads cannot exhaust memory ahead of the processing gate in app.ocr.service.
_MAX_INFLIGHT_UPLOADS = 3
_inflight = 0

# What has actually been exercised (docs/14 §9). Updated from test runs, never inferred from a flag.
VERIFICATION: dict[str, str] = {
    "paddleocr": "tested_real",  # in-process ONNX, synthetic printed fixtures, network blocked
    "surya": "tested_real",  # local worker via llama.cpp, synthetic printed fixtures
    "chandra": "tested_real_one_synthetic_page",  # MLX 8-bit; ONE synthetic handwriting-style page (a font), not real handwriting
    "rxnorm": "tested_real",  # local NLM prescribable-content index
}


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _ocr_enabled(settings: Settings = Depends(get_settings), db: aiosqlite.Connection = Depends(get_db)) -> Settings:
    if not settings.ocr_enabled:
        raise ApiError(404, "FEATURE_DISABLED", "Document reading is not enabled on this server")
    await service.retention_sweep(db, settings)  # expired documents are deleted before anything is served
    return settings


@router.get("/intake/document/capabilities")
async def capabilities(_: Principal = Depends(get_current_principal), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Which engines are enabled and installed. No model is loaded to answer this."""
    return {
        "ocr_enabled": settings.ocr_enabled,
        "processing": "on this server only — no document is sent to an outside company",
        "engines": {
            "paddleocr": {"enabled": settings.ocr_enabled, "ready": paddle.model_installed(settings.ocr_model_dir), "role": "printed text: words, boxes, scores"},
            "surya": {"enabled": settings.ocr_surya_enabled, "ready": settings.ocr_surya_enabled and settings.ocr_worker_python.is_file(), "role": "printed lab tables (second reading)"},
            "chandra": {"enabled": settings.ocr_chandra_enabled, "ready": settings.ocr_chandra_enabled and settings.ocr_worker_python.is_file(), "role": "handwritten prescriptions, discharge summaries"},
        },
        "document_types": {"lab_report": True, "prescription": settings.ocr_chandra_enabled, "discharge_summary": settings.ocr_chandra_enabled,
                           "xray_ecg": False},
        "max_bytes": settings.ocr_max_bytes,
        "retention_days": settings.ocr_retention_days,  # None = kept until a reviewer deletes the document
        "max_pages": service.MAX_PAGES,
        "verification": VERIFICATION,
    }


async def _read_multipart(request: Request, cap: int) -> dict[str, Any]:
    ctype, params = parse_options_header(request.headers.get("content-type") or "")
    if ctype != b"multipart/form-data" or b"boundary" not in params:
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Send the document as multipart/form-data")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > cap + _FORM_OVERHEAD:
        raise ApiError(413, "DOCUMENT_TOO_LARGE", "The document is too large")
    parts: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    header_field = bytearray()
    header_value = bytearray()

    def on_part_begin():
        current.clear()
        current.update({"headers": {}, "data": bytearray()})

    def on_header_field(data, start, end):
        header_field.extend(data[start:end])

    def on_header_value(data, start, end):
        header_value.extend(data[start:end])

    def on_header_end():
        current["headers"][bytes(header_field).lower()] = bytes(header_value)
        header_field.clear()
        header_value.clear()

    def on_part_data(data, start, end):
        current["data"].extend(data[start:end])
        if len(current["data"]) > cap:
            raise ApiError(413, "DOCUMENT_TOO_LARGE", "The document is too large")

    def on_part_end():
        parts.append(dict(current))
        if len(parts) > 4:
            raise ApiError(400, "VALIDATION_ERROR", "Unexpected form fields")

    parser = MultipartParser(params[b"boundary"], {
        "on_part_begin": on_part_begin, "on_header_field": on_header_field, "on_header_value": on_header_value,
        "on_header_end": on_header_end, "on_part_data": on_part_data, "on_part_end": on_part_end,
    })
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap + _FORM_OVERHEAD:
            raise ApiError(413, "DOCUMENT_TOO_LARGE", "The document is too large")
        parser.write(chunk)
    parser.finalize()
    fields: dict[str, Any] = {}
    for p in parts:
        _, disp = parse_options_header(p["headers"].get(b"content-disposition", b""))
        name = disp.get(b"name", b"").decode("latin-1")
        if name not in ("file", "case_id", "document_type", "idempotency_key") or name in fields:
            raise ApiError(400, "VALIDATION_ERROR", "Unexpected form fields")
        fields[name] = bytes(p["data"]) if name == "file" else bytes(p["data"]).decode("ascii", errors="strict")
    return fields


def _uuid(value: Any, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        raise ApiError(400, "VALIDATION_ERROR", "Request validation failed", {"fields": [name]}) from None


@router.post(
    "/intake/document",
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file", "case_id", "document_type", "idempotency_key"], "additionalProperties": False,
        "properties": {
            "file": {"type": "string", "format": "binary"},
            "case_id": {"type": "string", "format": "uuid"},
            "document_type": {"type": "string", "enum": list(_DOC_TYPES)},
            "idempotency_key": {"type": "string", "format": "uuid"},
        }}}}}},
)
async def upload_document(request: Request, principal: Principal = Depends(_uploaders), settings: Settings = Depends(_ocr_enabled),
                          db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    global _inflight
    if _inflight >= _MAX_INFLIGHT_UPLOADS:
        raise ApiError(503, "OCR_BUSY", "Document reading is busy; try again in a minute", {"reason": "too_many_uploads"})
    _inflight += 1
    try:
        return await _upload(request, principal, settings, db)
    finally:
        _inflight -= 1


async def _upload(request: Request, principal: Principal, settings: Settings, db: aiosqlite.Connection) -> dict[str, Any]:
    try:
        form = await _read_multipart(request, settings.ocr_max_bytes)
    except UnicodeDecodeError:
        raise ApiError(400, "VALIDATION_ERROR", "Request validation failed") from None
    except ApiError:
        raise
    except Exception:  # noqa: BLE001 - malformed multipart; never echo it
        raise ApiError(400, "VALIDATION_ERROR", "The upload could not be read") from None
    if set(form) != {"file", "case_id", "document_type", "idempotency_key"}:
        raise ApiError(400, "VALIDATION_ERROR", "Request validation failed", {"fields": sorted({"file", "case_id", "document_type", "idempotency_key"} - set(form))})
    if form["document_type"] not in _DOC_TYPES:
        raise ApiError(400, "VALIDATION_ERROR", "Request validation failed", {"fields": ["document_type"]})
    case_id = _uuid(form["case_id"], "case_id")
    key = _uuid(form["idempotency_key"], "idempotency_key")
    data: bytes = form.pop("file")
    engines = getattr(request.app.state, "ocr_engines", None)  # tests only; None in production
    return await service.upload(db, principal, case_id, data, form["document_type"], key, settings, _rid(request), engines=engines)


@router.get("/cases/{case_id}/documents")
async def list_documents(case_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal), _: Settings = Depends(_ocr_enabled),
                         db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return {"case_id": str(case_id), "documents": await service.list_documents(db, principal, str(case_id), _rid(request))}


@router.get("/cases/{case_id}/documents/reviewed")
async def reviewed(case_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers), _: Settings = Depends(_ocr_enabled),
                   db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.reviewed(db, principal, str(case_id), _rid(request))


@router.get("/cases/{case_id}/documents/{document_id}")
async def get_document(case_id: uuid.UUID, document_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal),
                       _: Settings = Depends(_ocr_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.get_document(db, principal, str(case_id), str(document_id), _rid(request))


@router.get("/cases/{case_id}/documents/{document_id}/pages/{page_index}/image", response_class=Response)
async def page_image(case_id: uuid.UUID, document_id: uuid.UUID, page_index: int, request: Request, principal: Principal = Depends(get_current_principal),
                     settings: Settings = Depends(_ocr_enabled), db: aiosqlite.Connection = Depends(get_db)) -> Response:
    if not 0 <= page_index < service.MAX_PAGES:
        raise ApiError(404, "NOT_FOUND", "Not found")
    data = await service.page_image(db, principal, str(case_id), str(document_id), page_index, settings, _rid(request))
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@router.post("/cases/{case_id}/documents/{document_id}/attestation")
async def attest(case_id: uuid.UUID, document_id: uuid.UUID, body: service.AttestationBody, request: Request, principal: Principal = Depends(_reviewers),
                 _: Settings = Depends(_ocr_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.attest(db, principal, str(case_id), str(document_id), body, _rid(request))


@router.post("/cases/{case_id}/documents/fields/{field_id}/review")
async def review(case_id: uuid.UUID, field_id: uuid.UUID, body: service.ReviewBody, request: Request, principal: Principal = Depends(_reviewers),
                 _: Settings = Depends(_ocr_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.review(db, principal, str(case_id), str(field_id), body, _rid(request))


@router.delete("/cases/{case_id}/documents/{document_id}")
async def delete_document(case_id: uuid.UUID, document_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers),
                          settings: Settings = Depends(_ocr_enabled), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.delete_document(db, principal, str(case_id), str(document_id), settings, _rid(request))
