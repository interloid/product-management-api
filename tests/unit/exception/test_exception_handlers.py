from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.exceptions.base import AppException
from app.exceptions.custom import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    InternalServerException,
    NotFoundException,
    ServiceUnavailableException,
    UnauthorizedException,
)
from app.exceptions.global_exception import AUTH_ERROR_RESPONSES, CRUD_ERROR_RESPONSES
from app.exceptions.handlers import (
    app_exception_handler,
    general_exception_handler,
    http_exception_handler,
    register_exception_handlers,
    validation_exception_handler,
)
from app.utils.helpers import request_id_ctx


def test_app_exception_sets_all_attributes():
    exception = AppException(
        message="Something went wrong",
        status_code=418,
        error_code="TEST_ERROR",
        details={"field": "value"},
    )

    assert exception.message == "Something went wrong"
    assert exception.status_code == 418
    assert exception.error_code == "TEST_ERROR"
    assert exception.details == {"field": "value"}
    assert str(exception) == "Something went wrong"


@pytest.mark.parametrize(
    (
        "exception_class",
        "expected_status",
        "expected_code",
        "expected_message",
    ),
    [
        (
            BadRequestException,
            status.HTTP_400_BAD_REQUEST,
            "BAD_REQUEST",
            "Bad request",
        ),
        (
            UnauthorizedException,
            status.HTTP_401_UNAUTHORIZED,
            "UNAUTHORIZED",
            "Unauthorized",
        ),
        (
            ForbiddenException,
            status.HTTP_403_FORBIDDEN,
            "FORBIDDEN",
            "Forbidden",
        ),
        (
            NotFoundException,
            status.HTTP_404_NOT_FOUND,
            "NOT_FOUND",
            "Resource not found",
        ),
        (
            ConflictException,
            status.HTTP_409_CONFLICT,
            "CONFLICT",
            "Conflict",
        ),
        (
            InternalServerException,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_SERVER_ERROR",
            "Internal server error",
        ),
        (
            ServiceUnavailableException,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Service temporarily unavailable",
        ),
    ],
)
def test_custom_exception_defaults(
    exception_class,
    expected_status,
    expected_code,
    expected_message,
):
    exception = exception_class()

    assert exception.status_code == expected_status
    assert exception.error_code == expected_code
    assert exception.message == expected_message
    assert exception.details is None


def test_custom_exception_accepts_message_and_details():
    exception = NotFoundException(
        message="Product not found",
        details={"product_id": "123"},
    )

    assert exception.message == "Product not found"
    assert exception.details == {"product_id": "123"}


@pytest.mark.asyncio
async def test_app_exception_handler_returns_expected_response():
    request = MagicMock(spec=Request)

    exception = ConflictException(
        message="SKU already exists",
        details={"sku": "IPHONE-15"},
    )

    request_id_token = request_id_ctx.set("request-123")

    try:
        with patch(
            "app.exceptions.handlers.logger.warning",
        ) as mock_logger:
            response = await app_exception_handler(
                request,
                exception,
            )

        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_409_CONFLICT

        body = response.body.decode()

        assert "SKU already exists" in body
        assert "CONFLICT" in body
        assert "request-123" in body

        mock_logger.assert_called_once_with(
            "%s | %s",
            "CONFLICT",
            "SKU already exists",
        )

    finally:
        request_id_ctx.reset(request_id_token)


@pytest.mark.asyncio
async def test_app_exception_handler_without_details():
    request = MagicMock(spec=Request)

    exception = NotFoundException(
        message="Product not found",
    )

    request_id_token = request_id_ctx.set("request-456")

    try:
        response = await app_exception_handler(
            request,
            exception,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

        body = response.body.decode()

        assert "Product not found" in body
        assert "NOT_FOUND" in body
        assert "request-456" in body

    finally:
        request_id_ctx.reset(request_id_token)


@pytest.mark.asyncio
async def test_http_exception_handler_returns_expected_response():
    request = MagicMock(spec=Request)

    exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied",
    )

    request_id_token = request_id_ctx.set("request-http")

    try:
        with patch(
            "app.exceptions.handlers.logger.warning",
        ) as mock_logger:
            response = await http_exception_handler(
                request,
                exception,
            )

        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        body = response.body.decode()

        assert "Access denied" in body
        assert "HTTP_EXCEPTION" in body
        assert "request-http" in body

        mock_logger.assert_called_once_with(
            "HTTP %s | %s",
            403,
            "Access denied",
        )

    finally:
        request_id_ctx.reset(request_id_token)


@pytest.mark.asyncio
async def test_general_exception_handler_returns_internal_server_error():
    request = MagicMock(spec=Request)

    exception = RuntimeError("Database connection failed")

    request_id_token = request_id_ctx.set("request-error")

    try:
        with patch(
            "app.exceptions.handlers.logger.exception",
        ) as mock_logger:
            response = await general_exception_handler(
                request,
                exception,
            )

        assert isinstance(response, JSONResponse)

        assert response.status_code == (status.HTTP_500_INTERNAL_SERVER_ERROR)

        body = response.body.decode()

        assert "Internal server error" in body
        assert "INTERNAL_SERVER_ERROR" in body
        assert "request-error" in body

        mock_logger.assert_called_once_with(
            "Unhandled exception: %s",
            exception,
        )

    finally:
        request_id_ctx.reset(request_id_token)


def test_register_exception_handlers():
    app = FastAPI()

    register_exception_handlers(app)

    assert AppException in app.exception_handlers
    assert HTTPException in app.exception_handlers
    assert RequestValidationError in app.exception_handlers
    assert Exception in app.exception_handlers

    assert app.exception_handlers[AppException] == app_exception_handler
    assert app.exception_handlers[HTTPException] == http_exception_handler
    assert (
        app.exception_handlers[RequestValidationError] == validation_exception_handler
    )
    assert app.exception_handlers[Exception] == general_exception_handler


def test_auth_error_responses_contains_expected_status_codes():
    expected_status_codes = {
        401,
        403,
        422,
        500,
        503,
    }

    assert set(AUTH_ERROR_RESPONSES.keys()) == expected_status_codes


def test_crud_error_responses_contains_expected_status_codes():
    expected_status_codes = {
        400,
        401,
        403,
        404,
        409,
        422,
        500,
        503,
    }

    assert set(CRUD_ERROR_RESPONSES.keys()) == expected_status_codes


def test_auth_error_responses_use_error_response_model():
    for response in AUTH_ERROR_RESPONSES.values():
        assert response["model"] is not None
        assert response["description"]


def test_crud_error_responses_use_error_response_model():
    for response in CRUD_ERROR_RESPONSES.values():
        assert response["model"] is not None
        assert response["description"]
