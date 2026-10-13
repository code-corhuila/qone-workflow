"""The single error envelope (norm 5.3.5, Annex C) and the ONE place where a domain or
application error becomes a status code; unknown routes and malformed JSON included."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from workflow.adapter.inbound.http.auth import AuthError
from workflow.application.errors import Forbidden, InvalidInput, NotFound

log = logging.getLogger("workflow.http")


class BorderError(Exception):
    """Several invalid inputs seen at the border at once: (field, message) pairs."""

    def __init__(self, details: list[tuple[str, str]]) -> None:
        super().__init__("the request has invalid fields")
        self.details = details


def envelope(
    request: Request, status: int, code: str, message: str, details: list[tuple[str, str]] | None = None
) -> JSONResponse:
    body: dict = {"error": code, "message": message, "traceId": trace_id(request)}
    if details:
        body["details"] = [{"field": field, "message": text} for field, text in details]
    return JSONResponse(body, status_code=status)


def trace_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "")


def install(app: FastAPI) -> None:
    @app.exception_handler(InvalidInput)
    async def _invalid(request: Request, error: InvalidInput):
        return envelope(
            request, 400, "VALIDATION_ERROR", "the request has invalid fields", [(error.field, error.message)]
        )

    @app.exception_handler(BorderError)
    async def _border(request: Request, error: BorderError):
        return envelope(request, 400, "VALIDATION_ERROR", "the request has invalid fields", error.details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, error: RequestValidationError):
        details = [(_field_of(e.get("loc", ())), str(e.get("msg", "invalid"))) for e in error.errors()]
        return envelope(request, 400, "VALIDATION_ERROR", "the request has invalid fields", details)

    @app.exception_handler(AuthError)
    async def _unauthorized(request: Request, error: AuthError):
        return envelope(request, 401, "UNAUTHORIZED", error.message)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, error: Forbidden):
        return envelope(request, 403, "FORBIDDEN", error.message)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, error: NotFound):
        return envelope(request, 404, "NOT_FOUND", error.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, error: StarletteHTTPException):
        if error.status_code in (404, 405):
            return envelope(request, 404, "NOT_FOUND", "resource not found")
        log.error("unexpected http exception", extra={"traceId": trace_id(request), "detail": str(error.detail)})
        return envelope(request, 500, "INTERNAL_ERROR", "unexpected error")


def internal_error(request: Request, error: Exception) -> JSONResponse:
    log.exception("unhandled error", extra={"traceId": trace_id(request)}, exc_info=error)
    return envelope(request, 500, "INTERNAL_ERROR", "unexpected error")


def _field_of(loc: tuple) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) if parts else (str(loc[0]) if loc else "body")
