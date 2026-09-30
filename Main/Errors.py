"""A single, consistent error contract for the whole API.

Every failure -- validation, authentication, missing resource, conflict, or
an unexpected server error -- is rendered as::

    {"error": {"code": "<machine readable>",
               "message": "<human readable>",
               "details": [...]}}   # details only for input validation

Status codes are conventional (401/403/404/409/422/500) but the *shape* is
what this module guarantees, so clients only need one parser.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """An error with a stable machine-readable code and an HTTP status."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_payload(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Build the response body for a failure."""
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return {"error": body}


# --------------------------------------------------------------------------
# Constructors -- use these instead of raising HTTPException directly so that
# codes stay consistent across routers.
# --------------------------------------------------------------------------
def unauthenticated(message: str = "Authentication is required.") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "unauthenticated", message)


def forbidden(message: str = "You do not have access to this resource.") -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "forbidden", message)


def not_found(resource: str = "Resource") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "not_found", f"{resource} not found.")


def conflict(code: str, message: str) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, code, message)


def invalid_input(message: str, details: list[dict[str, Any]] | None = None) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_input", message, details)


_HTTP_CODES = {
    status.HTTP_401_UNAUTHORIZED: "unauthenticated",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
}


def register_error_handlers(app: FastAPI) -> None:
    """Install handlers so every failure uses the same envelope."""

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "message": err.get("msg", "invalid value"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload("invalid_input", "Request validation failed.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, str(exc.detail)),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(
        _request: Request, _exc: IntegrityError
    ) -> JSONResponse:
        # Safety net: a constraint fired somewhere we did not check first.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=error_payload(
                "conflict", "The request violates a database constraint."
            ),
        )
