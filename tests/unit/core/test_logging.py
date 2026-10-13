from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.middleware import logging_middleware
from app.middleware.logging_middleware import LoggingMiddleware
from app.utils.helpers import (
    method_ctx,
    path_ctx,
    request_id_ctx,
)


@pytest.mark.asyncio
async def test_logging_middleware_logs_request_and_adds_request_id():
    request = MagicMock()
    request.method = "GET"
    request.url.path = "/api/v1/products"

    response = MagicMock()
    response.status_code = 200
    response.headers = {}

    call_next = AsyncMock(
        return_value=response,
    )

    middleware = LoggingMiddleware(app=MagicMock())

    with (
        patch.object(
            logging_middleware,
            "generate_request_id",
            return_value="request-123",
        ),
        patch.object(
            logging_middleware.logger,
            "info",
        ) as mock_logger,
    ):
        result = await middleware.dispatch(
            request,
            call_next,
        )

    assert result is response

    call_next.assert_awaited_once_with(request)

    assert response.headers["X-Request-ID"] == "request-123"

    assert mock_logger.call_count == 2

    mock_logger.assert_any_call(
        "Request started",
    )

    assert mock_logger.call_args_list[1].args[0] == (
        "Request completed | status_code=%s | duration=%.2f ms"
    )


@pytest.mark.asyncio
async def test_logging_middleware_sets_request_context():
    request = MagicMock()
    request.method = "POST"
    request.url.path = "/api/v1/products"

    response = MagicMock()
    response.status_code = 201
    response.headers = {}

    call_count = 0

    async def call_next(request):
        nonlocal call_count
        call_count += 1

        assert request_id_ctx.get() == "request-456"
        assert method_ctx.get() == "POST"
        assert path_ctx.get() == "/api/v1/products"

        return response

    middleware = LoggingMiddleware(app=MagicMock())

    with patch.object(
        logging_middleware,
        "generate_request_id",
        return_value="request-456",
    ):
        result = await middleware.dispatch(request, call_next)

    assert result is response
    assert call_count == 1


@pytest.mark.asyncio
async def test_logging_middleware_resets_context_after_request():
    request = MagicMock()

    request.method = "GET"
    request.url.path = "/health"

    response = MagicMock()
    response.status_code = 200
    response.headers = {}

    call_next = AsyncMock(
        return_value=response,
    )

    middleware = LoggingMiddleware(app=MagicMock())

    with patch.object(
        logging_middleware,
        "generate_request_id",
        return_value="request-789",
    ):
        await middleware.dispatch(
            request,
            call_next,
        )

    assert request_id_ctx.get("-") == "-"
    assert method_ctx.get("-") == "-"
    assert path_ctx.get("-") == "-"


@pytest.mark.asyncio
async def test_logging_middleware_resets_context_when_request_fails():
    request = MagicMock()

    request.method = "GET"
    request.url.path = "/api/v1/products"

    call_next = AsyncMock(
        side_effect=RuntimeError("Something went wrong"),
    )

    middleware = LoggingMiddleware(app=MagicMock())

    with patch.object(
        logging_middleware,
        "generate_request_id",
        return_value="request-error",
    ):
        with pytest.raises(
            RuntimeError,
            match="Something went wrong",
        ):
            await middleware.dispatch(
                request,
                call_next,
            )

    assert request_id_ctx.get("-") == "-"
    assert method_ctx.get("-") == "-"
    assert path_ctx.get("-") == "-"
