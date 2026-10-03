"""The error format (contracts/error-format.md) and the exception → HTTP mapping.

`STATUS_BY_CODE` is the only place where an error code gets its HTTP status. Every
non-2xx response, including Starlette's routing errors, validation errors and
unexpected exceptions, has the body `ErrorBody {error, detail, fields}`. Exception
text, causes and tracebacks only go to the server log.
"""

import logging
from typing import Any, get_args

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorBody, ErrorCode, FieldError
from app.errors import BudgieError, StorageError

logger = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, int] = {
    "bad_request": 400,
    "not_found": 404,
    "method_not_allowed": 405,
    "invalid_state": 409,
    "file_too_large": 413,
    "unsupported_file": 422,
    "uncategorized_items": 422,
    "incomplete_expense": 422,
    "validation_error": 422,
    "storage_error": 500,
    "internal_error": 500,
    "not_implemented": 501,
}

# Fixed descriptions: http.HTTPStatus phrases differ between Python versions (413),
# which would make docs/openapi.json depend on the interpreter.
_DESCRIPTIONS: dict[int, str] = {
    400: "Bad request",
    404: "Not found",
    405: "Method not allowed",
    409: "Invalid state",
    413: "File too large",
    422: "Validation or business-rule error",
    500: "Server error",
    501: "Not implemented yet",
}

# Starlette's own HTTP errors (routing, multipart parsing) by status. Any other status
# becomes 500 internal_error, because its detail isn't known to be safe to show.
_CODE_BY_HTTP_STATUS: dict[int, str] = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
}

_DEFAULT_HTTP_DETAIL: dict[str, str] = {
    "bad_request": "The request body could not be parsed.",
    "not_found": "The requested resource does not exist.",
    "method_not_allowed": "This method is not allowed for this path.",
}

# Starlette's generic phrases, replaced by the friendlier texts above.
_GENERIC_HTTP_DETAIL = frozenset({"Bad Request", "Not Found", "Method Not Allowed"})

_ERROR_CODES = frozenset(get_args(ErrorCode))

_INTERNAL_DETAIL = "An unexpected error occurred."
_VALIDATION_DETAIL = "The request is invalid."


def error_response(
    status: int,
    code: str,
    detail: str,
    fields: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    if code not in _ERROR_CODES:
        # An undocumented code must not break the error format itself.
        logger.error("Unknown error code %r answered as internal_error", code)
        status, code, detail, fields = 500, "internal_error", _INTERNAL_DETAIL, None
    body = ErrorBody.model_validate({"error": code, "detail": detail, "fields": fields})
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=headers)


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """`responses=` entries documenting `ErrorBody` for the given statuses."""
    return {
        status: {"model": ErrorBody, "description": _DESCRIPTIONS.get(status, "Error")}
        for status in statuses
    }


def _field_name(loc: tuple[int | str, ...] | list[int | str]) -> str:
    parts = list(loc)
    if len(parts) > 1 and parts[0] == "body":
        parts = parts[1:]
    return ".".join(str(part) for part in parts)


async def _budgie_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, BudgieError)
    status = STATUS_BY_CODE.get(exc.code)
    if status is None:
        # A subclass whose code is missing from the map: its detail isn't vetted either.
        logger.exception("%s has unmapped code %r", type(exc).__name__, exc.code, exc_info=exc)
        return error_response(500, "internal_error", _INTERNAL_DETAIL)
    # Storage and other server-side errors keep their cause and traceback in the log only.
    if isinstance(exc, StorageError) or (status >= 500 and exc.code != "not_implemented"):
        logger.exception("%s: %s", type(exc).__name__, exc.detail, exc_info=exc)
    return error_response(status, exc.code, exc.detail)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _CODE_BY_HTTP_STATUS.get(exc.status_code)
    if code is None:
        logger.error("Unmapped HTTP %s answered as internal_error", exc.status_code)
        return error_response(500, "internal_error", _INTERNAL_DETAIL)
    detail = exc.detail if isinstance(exc.detail, str) else ""
    if not detail or detail in _GENERIC_HTTP_DETAIL:
        detail = _DEFAULT_HTTP_DETAIL[code]
    return error_response(exc.status_code, code, detail, headers=exc.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = [
        FieldError(field=_field_name(err.get("loc", ())), message=str(err.get("msg", "")))
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", _VALIDATION_DETAIL, fields)


async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return error_response(500, "internal_error", _INTERNAL_DETAIL)


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers that turn every error into `ErrorBody`."""
    app.add_exception_handler(BudgieError, _budgie_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected_error)
