from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from ..domain.video import VideoIngestError
from ..errors import (
    RepositoryConflictError,
    RepositoryDuplicateError,
    RepositoryError,
    RepositoryNotFoundError,
)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict = Field(default_factory=dict)
    retryable: bool = False
    trace_id: str = Field(min_length=1)


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


def error_response(
    code: str,
    message: str,
    *,
    status_code: int,
    details: dict | None = None,
    retryable: bool = False,
) -> JSONResponse:
    payload = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or {},
            retryable=retryable,
            trace_id=str(uuid4()),
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def map_domain_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, VideoIngestError):
        code = exc.code.value
        if code == "FILE_NOT_FOUND":
            code = "VIDEO_NOT_FOUND"
        status = 413 if code == "FILE_TOO_LARGE" else 400
        return error_response(code, str(exc), status_code=status)
    if isinstance(exc, RepositoryNotFoundError):
        rendered = str(exc)
        code = (
            "VIDEO_NOT_FOUND"
            if "video" in rendered
            else "ANALYSIS_NOT_FOUND"
            if "analysis" in rendered
            else "EVENT_NOT_FOUND"
        )
        return error_response(code, str(exc), status_code=404)
    if isinstance(exc, RepositoryDuplicateError):
        return error_response("DUPLICATE_RECORD", str(exc), status_code=409)
    if isinstance(exc, RepositoryConflictError):
        return error_response("REVISION_CONFLICT", str(exc), status_code=409)
    if isinstance(exc, RepositoryError):
        return error_response(exc.code, str(exc), status_code=500, retryable=True)
    return error_response("INTERNAL_ERROR", "Beklenmeyen local backend hatası.", status_code=500)


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        "INVALID_REQUEST",
        "İstek sözleşmesi geçersiz.",
        status_code=422,
        details={"errors": exc.errors()},
    )


async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:

    detail = exc.detail
    message = detail if isinstance(detail, str) else "HTTP isteği tamamlanamadı."
    return error_response(
        {
            400: "INVALID_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            413: "FILE_TOO_LARGE",
            422: "INVALID_REQUEST",
            503: "MODEL_UNAVAILABLE",
        }.get(exc.status_code, "HTTP_ERROR"),
        message,
        status_code=exc.status_code,
        details={"detail": detail},
    )


async def domain_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return map_domain_error(exc)


__all__ = [
    "ErrorBody",
    "ErrorEnvelope",
    "domain_exception_handler",
    "error_response",
    "http_exception_handler",
    "map_domain_error",
    "validation_exception_handler",
]
