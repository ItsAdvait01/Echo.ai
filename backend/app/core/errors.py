"""
Consistent error handling and exception mapping.  Owned by A1.

All API errors return ``ErrorDetail`` (code, message, retryable) per TRD §10.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.schemas.contracts import ErrorDetail


# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------

class EchoError(Exception):
    """Base for all Echo.ai domain errors."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 500,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class NotFoundError(EchoError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            code="not_found",
            message=f"{resource} '{resource_id}' not found.",
            status_code=404,
        )


class ConsentRequiredError(EchoError):
    def __init__(self) -> None:
        super().__init__(
            code="consent_required",
            message="Explicit user consent is required before observation.",
            status_code=400,
        )


class InvalidStateError(EchoError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="invalid_state",
            message=detail,
            status_code=409,
        )


class ServiceUnavailableError(EchoError):
    def __init__(self, service: str) -> None:
        super().__init__(
            code="service_unavailable",
            message=f"The {service} service is not available.",
            status_code=503,
            retryable=True,
        )


# ---------------------------------------------------------------------------
# FastAPI exception handlers — registered in main.py
# ---------------------------------------------------------------------------

async def echo_error_handler(_request: Request, exc: EchoError) -> JSONResponse:
    detail = ErrorDetail(code=exc.code, message=exc.message, retryable=exc.retryable)
    return JSONResponse(status_code=exc.status_code, content=detail.model_dump())


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = ErrorDetail(
        code="http_error",
        message=str(exc.detail),
        retryable=exc.status_code >= 500,
    )
    return JSONResponse(status_code=exc.status_code, content=detail.model_dump())


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    detail = ErrorDetail(
        code="internal_error",
        message="An unexpected error occurred.",
        retryable=True,
    )
    return JSONResponse(status_code=500, content=detail.model_dump())
