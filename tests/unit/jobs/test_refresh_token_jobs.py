from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arq import Retry

from app.jobs.refresh_token_jobs import cleanup_expired_refresh_tokens_jobs


@pytest.mark.asyncio
async def test_cleanup_expired_refresh_tokens_jobs_success():

    db = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = db

    transaction = MagicMock()
    db.begin.return_value = transaction
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)

    with patch("app.jobs.refresh_token_jobs.RefreshTokenRepository") as mock_repository:
        repository = mock_repository.return_value
        repository.delete_expired = AsyncMock(return_value=5)

        ctx = {"db_session_factory": session_factory}

        await cleanup_expired_refresh_tokens_jobs(ctx)

        mock_repository.assert_called_once_with(db)
        repository.delete_expired.assert_awaited_once()

        passed_now = repository.delete_expired.await_args.kwargs["now"]
        assert isinstance(passed_now, datetime)
        assert passed_now.tzinfo == UTC


@pytest.mark.asyncio
async def test_cleanup_expired_refresh_tokens_failure():
    db = MagicMock()

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock(return_value=session)

    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    db.begin.return_value = transaction

    with patch("app.jobs.refresh_token_jobs.RefreshTokenRepository") as mock_repository:
        repository = mock_repository.return_value
        repository.delete_expired = AsyncMock(
            side_effect=RuntimeError("Database unavailable")
        )

        ctx = {
            "db_session_factory": session_factory,
            "job_try": 2,
        }

        with pytest.raises(Retry) as exc_info:
            await cleanup_expired_refresh_tokens_jobs(ctx)

        assert exc_info.value.defer_score == 60000
        repository.delete_expired.assert_awaited_once()
