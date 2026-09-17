from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ExceptionHandler

from app.core.logging import get_logger
from app.exceptions.base import AppException
from app.schemas.response import ErrorDetail, ErrorResponse
from app.utils.helpers import request_id_ctx

logger = get_logger(__name__)


async def app_exception_handler(
    request: Request,
    exc: AppException,
):
    logger.warning(
        "%s | %s",
        exc.error_code,
        exc.message,
    )

    response = ErrorResponse(
        message=exc.message,
        error=ErrorDetail(
            code=exc.error_code,
            details=exc.details,
        ),
        request_id=request_id_ctx.get(),
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=response.model_dump(),
    )


def format_validation_message(
    error: dict[str, Any],
    field: str,
) -> str:
    error_type = error.get("type")
    context = error.get("ctx", {})

    if error_type == "missing":
        return "This field is required"

    if error_type == "string_too_short":
        min_length = context.get("min_length")
        return f"Must contain at least {min_length} characters"

    if error_type == "string_too_long":
        max_length = context.get("max_length")
        return f"Must be at most {max_length} characters"

    if error_type == "string_pattern_mismatch":
        return "Invalid format"

    if field == "email" and error_type == "value_error":
        return "Enter a valid email address"

    return str(error.get("msg", "Invalid value"))


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
):
    logger.warning(
        "HTTP %s | %s",
        exc.status_code,
        exc.detail,
    )

    response = ErrorResponse(
        message=str(exc.detail),
        error=ErrorDetail(
            code="HTTP_EXCEPTION",
        ),
        request_id=request_id_ctx.get(),
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=response.model_dump(),
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    details: dict[str, str] = {}

    for error in exc.errors():
        location = [
            str(part)
            for part in error.get("loc", ())
            if part
            not in {
                "body",
                "query",
                "path",
                "header",
                "cookie",
            }
        ]

        field = ".".join(location) or "request"

        details.setdefault(
            field,
            format_validation_message(
                error=error,
                field=field,
            ),
        )

    logger.warning(
        "Validation failed | fields=%s",
        list(details),
    )

    response = ErrorResponse(
        message="Validation failed",
        error=ErrorDetail(
            code="VALIDATION_ERROR",
            details=details,
        ),
        request_id=request_id_ctx.get(),
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=response.model_dump(mode="json"),
    )


async def general_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled exception: %s",
        exc,
    )

    response = ErrorResponse(
        message="Internal server error",
        error=ErrorDetail(
            code="INTERNAL_SERVER_ERROR",
        ),
        request_id=request_id_ctx.get(),
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=response.model_dump(),
    )


def register_exception_handlers(app: FastAPI):

    app.add_exception_handler(
        AppException,
        cast(ExceptionHandler, app_exception_handler),
    )

    app.add_exception_handler(
        HTTPException,
        cast(ExceptionHandler, http_exception_handler),
    )

    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_exception_handler),
    )

    app.add_exception_handler(
        Exception,
        general_exception_handler,
    )
