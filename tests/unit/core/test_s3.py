from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.s3 import S3Service


@pytest.mark.asyncio
async def test_upload_file_uploads_file_to_s3():
    data = b"fake image"
    object_key = "products/123/images/image.jpg"
    content_type = "image/jpeg"

    s3_client = MagicMock()
    s3_client.put_object = AsyncMock()
    service = S3Service(client=s3_client)
    service.bucket_name = "my-product-bucket"

    result = await service.upload_file(
        data=data,
        object_key=object_key,
        content_type=content_type,
    )

    assert result is None

    s3_client.put_object.assert_awaited_once_with(
        Bucket="my-product-bucket",
        Key=object_key,
        Body=data,
        ContentType="image/jpeg",
    )


@pytest.mark.asyncio
async def test_delete_file_deletes_object_from_s3():
    object_key = "products/123/images/image.jpg"

    s3_client = MagicMock()
    s3_client.delete_object = AsyncMock()
    service = S3Service(client=s3_client)
    service.bucket_name = "my-product-bucket"

    result = await service.delete_file(
        object_key=object_key,
    )

    assert result is None

    s3_client.delete_object.assert_awaited_once_with(
        Bucket="my-product-bucket",
        Key=object_key,
    )


@pytest.mark.asyncio
async def test_upload_file_propagates_s3_error():
    data = b"fake image"

    s3_client = MagicMock()
    s3_client.put_object = AsyncMock(
        side_effect=RuntimeError("S3 upload failed"),
    )
    service = S3Service(client=s3_client)

    with pytest.raises(RuntimeError, match="S3 upload failed"):
        await service.upload_file(
            data=data,
            object_key="products/image.jpg",
            content_type="image/jpeg",
        )
