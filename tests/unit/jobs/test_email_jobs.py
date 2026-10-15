import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arq import Retry

from app.jobs.email_jobs import send_passcode_email_job


@pytest.mark.asyncio
async def test_send_passcode_email_job_success():

    email_job_id = "test-job-123"
    payload_key = f"jobs:passcode-email:{email_job_id}"
    sent_key = f"jobs:passcode-email:sent:{email_job_id}"

    email_data = {
        "to_email": "user@example.com",
        "first_name": "User",
        "passcode": "123456",
        "expiry_minutes": 5,
    }

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.get = AsyncMock(return_value=json.dumps(email_data))

    pipeline = MagicMock()
    pipeline.set.return_value = pipeline
    pipeline.delete.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[True, 1])
    redis.pipeline.return_value = pipeline

    ctx = {"redis": redis}

    with patch(
        "app.jobs.email_jobs.send_passcode_email",
        new_callable=AsyncMock,
    ) as mock_send_email:
        result = await send_passcode_email_job(ctx, email_job_id)

    assert result == {
        "status": "sent",
        "email_job_id": email_job_id,
    }

    redis.exists.assert_awaited_once_with(sent_key)
    redis.get.assert_awaited_once_with(payload_key)

    mock_send_email.assert_awaited_once_with(
        to_email="user@example.com",
        first_name="User",
        passcode="123456",
        expiry_minutes=5,
    )

    redis.pipeline.assert_called_once_with(transaction=True)
    pipeline.set.assert_called_once_with(sent_key, "1", ex=86_400)
    pipeline.delete.assert_called_once_with(payload_key)
    pipeline.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_passcode_email_job_already_sent():
    email_job_id = "test-job-123"
    sent_key = f"jobs:passcode-email:sent:{email_job_id}"

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=1)
    redis.get = AsyncMock()

    ctx = {"redis": redis}

    with patch(
        "app.jobs.email_jobs.send_passcode_email",
        new_callable=AsyncMock,
    ) as mock_send_email:
        result = await send_passcode_email_job(ctx, email_job_id)

    assert result == {
        "status": "skipped",
        "reason": "already_sent",
    }

    redis.exists.assert_awaited_once_with(sent_key)
    redis.get.assert_not_awaited()
    mock_send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_passcode_email_job_retries_on_email_failure():
    email_job_id = "test-job-123"

    email_data = {
        "to_email": "user@example.com",
        "first_name": "Naveen",
        "passcode": "123456",
        "expiry_minutes": 5,
    }

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.get = AsyncMock(return_value=json.dumps(email_data))

    ctx = {
        "redis": redis,
        "job_try": 1,
    }

    with patch(
        "app.jobs.email_jobs.send_passcode_email",
        new_callable=AsyncMock,
        side_effect=RuntimeError("SMTP server unavailable"),
    ) as mock_send_email:
        with pytest.raises(Retry) as exc_info:
            await send_passcode_email_job(ctx, email_job_id)

    mock_send_email.assert_awaited_once()

    assert exc_info.value.defer_score == 10_000


@pytest.mark.asyncio
async def test_send_passcode_email_job_payload_not_found():
    email_job_id = "test-job-123"
    payload_key = f"jobs:passcode-email:{email_job_id}"

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.get = AsyncMock(return_value=None)

    ctx = {"redis": redis}

    with patch(
        "app.jobs.email_jobs.send_passcode_email",
        new_callable=AsyncMock,
    ) as mock_send_email:
        result = await send_passcode_email_job(ctx, email_job_id)

    assert result == {
        "status": "skipped",
        "reason": "payload_not_found",
    }

    redis.get.assert_awaited_once_with(payload_key)
    mock_send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_passcode_email_job_invalid_payload():
    email_job_id = "test-job-123"

    invalid_data = {
        "to_email": "user@example.com",
        "first_name": "Naveen",
        "expiry_minutes": 5,
    }

    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.get = AsyncMock(return_value=json.dumps(invalid_data))

    ctx = {"redis": redis}

    with patch(
        "app.jobs.email_jobs.send_passcode_email",
        new_callable=AsyncMock,
    ) as mock_send_email:
        with pytest.raises(
            ValueError,
            match="Invalid passcode email payload",
        ):
            await send_passcode_email_job(ctx, email_job_id)

    mock_send_email.assert_not_awaited()
