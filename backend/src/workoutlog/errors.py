"""One error shape for the whole API: {"error": {"code", "message"}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("workoutlog")


class ApiError(Exception):
    """Every deliberate error the API returns. status is the HTTP code, code is
    the stable string the frontend switches on."""

    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


def bad_request(message: str, code: str = "bad_request") -> ApiError:
    return ApiError(400, code, message)


def conflict(message: str, code: str = "conflict") -> ApiError:
    return ApiError(409, code, message)


def _envelope(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}},
                        headers=headers or {})


def _readable(errors: list[dict]) -> str:
    """Turn Pydantic's error list into one sentence a person can act on."""
    parts = []
    for err in errors[:3]:
        location = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        message = err.get("msg", "invalid value")
        message = message.removeprefix("Value error, ")
        parts.append(f"{location}: {message}" if location else message)
    if len(errors) > 3:
        parts.append(f"and {len(errors) - 3} more")
    return "; ".join(parts) or "Invalid request."


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return _envelope(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        return _envelope(422, "validation_error", _readable(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException):
        codes = {400: "bad_request", 401: "unauthorized", 403: "forbidden",
                 404: "not_found", 405: "method_not_allowed", 409: "conflict",
                 413: "too_large", 429: "rate_limited"}
        code = codes.get(exc.status_code, "error")
        return _envelope(exc.status_code, code, str(exc.detail), dict(exc.headers or {}))

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception):
        # Log the type, never the payload: request bodies carry notes and transcripts.
        log.exception("unhandled %s", type(exc).__name__)
        return _envelope(500, "internal_error", "Something went wrong. Try again.")
