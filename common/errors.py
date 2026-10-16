from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


log = logging.getLogger("errors")


class ApiError(Exception):
    """Domain error carrying the wire status + machine-readable code."""

    def __init__(
        self,
        status_code: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:

        super().__init__(message)

        self.status_code = status_code
        self.code = _STATUS_CODES.get(status_code, "ERROR")
        self.message = message
        self.details = details


    def body(self) -> dict[str, Any]:
        """Render this error as the JSON body sent to the client."""

        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details

        return {"error": error}


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers that render errors as the uniform `{"error": {...}}` body."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        err = ApiError(
            status_code=422,
            message="Request validation failed",
            details={"errors": _jsonable_errors(exc.errors())},
        )
        return JSONResponse(status_code=err.status_code, content=err.body())

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        err = ApiError(status_code=500, message="Internal server error", details={"error": repr(exc)})
        return JSONResponse(status_code=err.status_code, content=err.body())


def _jsonable_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the non-serializable `ctx`/`input` parts pydantic attaches to errors."""
    return [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in errors
    ]


_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "UNPROCESSABLE",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_FAILURE",
    503: "NOT_READY",
    504: "UPSTREAM_TIMEOUT",
}
