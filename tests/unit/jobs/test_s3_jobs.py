from unittest.mock import AsyncMock

import pytest
from arq import Retry

from app.core.settings import settings
from app.jobs.s3_jobs import delete_s3_jobs


@pytest.mark.asyncio
async def test_delete_s3_jobs_with_success():
    s3 = AsyncMock()
    ctx = {"s3": s3}

    object_keys = [
        "products/image1.jpg",
        "products/image2.jpg",
    ]

    await delete_s3_jobs(ctx, object_keys)

    assert s3.delete_object.await_count == 2

    s3.delete_object.assert_any_await(
        Bucket=settings.S3_BUCKET_NAME,
        Key="products/image1.jpg",
    )

    s3.delete_object.assert_any_await(
        Bucket=settings.S3_BUCKET_NAME,
        Key="products/image2.jpg",
    )


@pytest.mark.asyncio
async def test_delete_s3_jobs_failure_triggers_retry():
    s3 = AsyncMock()
    s3.delete_object.side_effect = RuntimeError("s3 Unavailable")

    ctx = {
        "s3": s3,
        "job_try": 1,
    }

    object_keys = [
        "products/image1.jpg",
    ]

    with pytest.raises(Retry) as exc_info:
        await delete_s3_jobs(ctx, object_keys)

    assert exc_info.value.defer_score == 10000

    s3.delete_object.assert_any_await(
        Bucket=settings.S3_BUCKET_NAME,
        Key="products/image1.jpg",
    )
