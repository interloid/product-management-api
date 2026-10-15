from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.utils.cache_key import build_product_cache_key


@pytest.mark.asyncio
async def test_upload_product_images_deletes_correct_product_detail_cache_key():

    product_uuid = uuid4()
    image_id = uuid4()

    expected_cache_key = build_product_cache_key(id=product_uuid)

    assert expected_cache_key == f"pms:product:detail:{product_uuid}"

    staging_key = f"staging/products/{product_uuid}/images/{image_id}.jpg"
    final_key = f"products/{product_uuid}/images/{image_id}.jpg"

    redis = MagicMock()
    redis.delete = AsyncMock()
    redis.smembers = AsyncMock(return_value=set())

    s3 = MagicMock()
    s3.copy_object = AsyncMock()
    s3.delete_object = AsyncMock()

    product = MagicMock()
    product.id = product_uuid

    saved_image = MagicMock()
    saved_image.id = image_id
    saved_image.product_id = product_uuid
    saved_image.object_key = final_key
    saved_image.content_hash = "a" * 64
    saved_image.is_primary = True

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_begin = MagicMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    mock_product_repo = MagicMock()
    mock_product_repo.get_by_id = AsyncMock(return_value=product)

    mock_image_repo = MagicMock()
    mock_image_repo.get_by_product_id = AsyncMock(return_value=[saved_image])
    mock_image_repo.set_primary = AsyncMock()

    def session_factory():
        return mock_session

    event_service = MagicMock()
    event_service.publish = AsyncMock()

    images = [
        {
            "image_id": str(image_id),
            "staging_key": staging_key,
            "extension": "jpg",
            "content_type": "image/jpeg",
            "content_hash": "a" * 64,
            "is_primary": True,
        }
    ]

    ctx = {
        "redis": redis,
        "s3": s3,
        "db_session_factory": session_factory,
    }

    with (
        patch(
            "app.jobs.image_jobs.ProductRepository",
            return_value=mock_product_repo,
        ),
        patch(
            "app.jobs.image_jobs.ProductImageRepository",
            return_value=mock_image_repo,
        ),
        patch(
            "app.jobs.image_jobs.EventService",
            return_value=event_service,
        ),
    ):
        from app.jobs.image_jobs import upload_product_images

        await upload_product_images(
            ctx=ctx,
            product_id=str(product_uuid),
            images=images,
            request_id="req-test-1",
        )

    all_delete_calls = redis.delete.call_args_list
    deleted_args = [arg for call in all_delete_calls for arg in call.args]

    assert expected_cache_key in deleted_args, (
        f"Expected cache key '{expected_cache_key}' to be deleted, "
        f"but redis.delete was called with: {deleted_args}"
    )

    broken_key = f"pms:product:detail{product_uuid}"
    assert broken_key not in deleted_args, (
        f"Broken cache key '{broken_key}' (missing colon) was used — "
        "build_product_cache_key() must be used instead"
    )
