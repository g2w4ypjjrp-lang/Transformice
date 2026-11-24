"""Uniform error responses and translation of database errors.

Database messages are never forwarded to clients (they may contain values);
clients receive a stable `code` and a generic message.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import errors as pg
from sqlalchemy.exc import DBAPIError

log = logging.getLogger("maliscan.errors")


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def not_found(entity: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"{entity} not found")


def forbidden(message: str = "insufficient permissions") -> ApiError:
    return ApiError(403, "forbidden", message)


_DB_ERROR_MAP: list[tuple[type[Exception], int, str, str]] = [
    (pg.InsufficientPrivilege, 403, "forbidden", "insufficient permissions"),
    (pg.UniqueViolation, 409, "conflict", "a record with the same unique values already exists"),
    (pg.ForeignKeyViolation, 422, "invalid_reference", "referenced record does not exist"),
    (pg.CheckViolation, 422, "constraint_violation", "the data violates a business rule"),
    (pg.NotNullViolation, 422, "constraint_violation", "a required value is missing"),
    (pg.InvalidTextRepresentation, 422, "invalid_value", "invalid value format"),
]


_MESSAGE_CODES = {
    "plan_limit:max_taxpayers": (402, "plan_limit_exceeded", "taxpayer limit of the subscription plan reached"),
    "plan_limit:max_users": (402, "plan_limit_exceeded", "user limit of the subscription plan reached"),
    "plan_limit:max_documents_per_month": (402, "plan_limit_exceeded", "monthly document limit reached"),
    "plan_limit:trial_expired": (402, "subscription_inactive", "trial period has ended"),
    "plan_limit:no_active_subscription": (402, "subscription_inactive", "no active subscription"),
    "invitation_invalid": (404, "invitation_invalid", "invitation is invalid, expired or already used"),
    "invitation_email_mismatch": (403, "invitation_email_mismatch", "invitation was sent to a different email"),
}


def translate_db_error(exc: DBAPIError) -> ApiError | None:
    orig = exc.orig
    primary = getattr(getattr(orig, "diag", None), "message_primary", None) or ""
    if primary in _MESSAGE_CODES:
        status, code, message = _MESSAGE_CODES[primary]
        return ApiError(status, code, message)
    for klass, status, code, message in _DB_ERROR_MAP:
        if isinstance(orig, klass):
            return ApiError(status, code, message)
    return None


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Only locations and types — never echo submitted values back into logs.
        fields = [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {"error": {"code": "validation_error", "message": "invalid request", "fields": fields}},
            status_code=422,
        )

    @app.exception_handler(DBAPIError)
    async def _db(_: Request, exc: DBAPIError) -> JSONResponse:
        api = translate_db_error(exc)
        if api is None:
            log.error("unhandled database error", extra={"error_code": type(exc.orig).__name__})
            return JSONResponse({"error": {"code": "internal_error", "message": "internal error"}}, status_code=500)
        return JSONResponse({"error": api.detail}, status_code=api.status_code)
