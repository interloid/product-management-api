from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.db.session import get_db
from app.exceptions.custom import ServiceUnavailableException


@pytest.mark.asyncio
async def test_get_db_maps_pool_timeout_to_service_unavailable() -> None:
    db = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=db)
    session_context.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.db.session.SessionLocal", return_value=session_context),
        patch("app.db.session.logger.error") as mock_logger,
    ):
        dependency = get_db()

        assert await anext(dependency) is db

        pool_error = SQLAlchemyTimeoutError("QueuePool limit reached")

        with pytest.raises(ServiceUnavailableException) as exc_info:
            await dependency.athrow(pool_error)

    assert exc_info.value.status_code == 503
    assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
    assert exc_info.value.message == "Database temporarily unavailable"
    db.rollback.assert_awaited_once_with()
    db.commit.assert_not_awaited()
    mock_logger.assert_called_once_with(
        "Database connection pool exhausted | error=%s",
        pool_error,
    )


@pytest.mark.asyncio
async def test_get_db_preserves_non_pool_exceptions() -> None:
    db = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=db)
    session_context.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.SessionLocal", return_value=session_context):
        dependency = get_db()

        assert await anext(dependency) is db

        original_error = RuntimeError("query failed")

        with pytest.raises(RuntimeError) as exc_info:
            await dependency.athrow(original_error)

    assert exc_info.value is original_error
    db.rollback.assert_awaited_once_with()
    db.commit.assert_not_awaited()
