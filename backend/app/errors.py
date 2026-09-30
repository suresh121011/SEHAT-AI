"""Uniform error envelope from docs/06_API_Data_Contracts.md §5."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_STATUS_TO_CODE = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "TRIAGE_LOCKED",
    422: "VALIDATION_ERROR",
}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


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
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "constraint": e["msg"]} for e in exc.errors()]
        return _envelope(request, 400, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "HTTP_ERROR")
        return _envelope(request, exc.status_code, code, str(exc.detail))
