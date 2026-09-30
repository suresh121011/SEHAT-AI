"""Uniform error envelope (docs/06 §5) and error-path hygiene (docs/11 §G).

Error responses and logs carry fixed messages and reason codes only, never request values.
"""

import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("sehat.errors")

_STATUS_TO_CODE = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
}

# Fixed, value-free messages keyed by pydantic error type. Pydantic's own `msg` can quote input
# (e.g. uuid_parsing quotes the offending character), so it is never passed through.
_VALIDATION_MESSAGES = {
    "missing": "Field required",
    "extra_forbidden": "Unexpected field",
    "string_pattern_mismatch": "Invalid format",
    "string_too_short": "Too short",
    "string_too_long": "Too long",
    "too_short": "Too few items",
    "too_long": "Too many items",
    "greater_than": "Value out of allowed range",
    "greater_than_equal": "Value out of allowed range",
    "less_than": "Value out of allowed range",
    "less_than_equal": "Value out of allowed range",
    "finite_number": "Value out of allowed range",
    "enum": "Value not allowed",
    "literal_error": "Value not allowed",
    "uuid_parsing": "Invalid identifier",
    "uuid_type": "Invalid identifier",
    "json_invalid": "Malformed JSON",
    "model_attributes_type": "Invalid object",
    "dict_type": "Invalid object",
    "list_type": "Invalid list",
}
_TYPE_ERROR_SUFFIXES = ("_type", "_parsing")


def _validation_message(error_type: str) -> str:
    if error_type in _VALIDATION_MESSAGES:
        return _VALIDATION_MESSAGES[error_type]
    if error_type.endswith(_TYPE_ERROR_SUFFIXES):
        return "Invalid type"
    return "Invalid value"


def _validation_field(error: dict[str, Any]) -> str:
    if error["type"] == "extra_forbidden":
        return "<extra>"  # the key name is client-controlled and may itself be personal data
    return ".".join(str(p) for p in error["loc"][1:])


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


# Named errors used by the privacy/consent layer (docs/06 §5).
def consent_required() -> ApiError:
    return ApiError(403, "CONSENT_REQUIRED", "Consent for this purpose is not in effect")


def not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Resource not found")


def _envelope(request: Request, status_code: int, code: str, message: str, details: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "request_id", None),
        }
    }
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return _envelope(request, exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default 422 is mapped to the contract's VALIDATION_ERROR (400).
        errors = [{"field": _validation_field(e), "constraint": _validation_message(e["type"])} for e in exc.errors()]
        return _envelope(request, 400, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "HTTP_ERROR")
        return _envelope(request, exc.status_code, code, str(exc.detail))


class SafeErrorMiddleware:
    """Outermost ASGI middleware: turns any unhandled exception into the generic 500 envelope.

    Starlette's ServerErrorMiddleware re-raises after calling a 500 handler, so uvicorn would log the
    full traceback (including exception messages that may hold request data). This middleware does not
    re-raise: it logs only the exception class and request id. It reduces exposure in logs; it does not
    guarantee that no other component retains the exception in memory.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_tracking(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        error_type: str | None = None
        try:
            await self.app(scope, receive, send_tracking)
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all at the outermost layer
            error_type = type(exc).__name__
        if error_type is None:
            return

        request_id = (scope.get("state") or {}).get("request_id") or str(uuid.uuid4())
        logger.error("unhandled_error type=%s request_id=%s", error_type, request_id)
        if response_started:
            return  # headers already sent: cannot replace the response; the server closes it
        body = json.dumps(
            {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error", "details": {}, "request_id": request_id}}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"application/json"), (b"x-request-id", request_id.encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
