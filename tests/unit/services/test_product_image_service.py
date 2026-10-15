from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.models.product_image_model import ProductImage
from app.services.product_image_service import ProductImageService


def make_service():
    repo = MagicMock()
    s3 = MagicMock()
    s3.upload_file = AsyncMock()
    s3.delete_file = AsyncMock()
    repo.create = AsyncMock(side_effect=lambda image: image)
    repo.set_primary = AsyncMock(side_effect=lambda image: image)
    repo.delete = AsyncMock()
    return ProductImageService(repo, s3), repo, s3


@pytest.mark.asyncio
async def test_upload_image_hashes_data_and_sets_primary():
    service, repo, s3 = make_service()
    product_id = uuid4()

    image = await service.upload_image(
        product_id=product_id,
        data=b"content",
        filename="photo.png",
        content_type="image/png",
        is_primary=True,
    )

    assert isinstance(image, ProductImage)
    assert image.product_id == product_id
    assert image.content_hash == (
        "ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73"
    )
    assert image.is_primary is False
    repo.set_primary.assert_awaited_once_with(image)
    s3.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_image_cleans_up_after_repository_create_failure():
    service, repo, s3 = make_service()
    repo.create.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.upload_image(
            product_id=uuid4(),
            data=b"content",
            filename="photo.png",
            content_type="image/png",
            content_hash="hash",
        )

    s3.delete_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_image_reraises_when_cleanup_fails():
    service, repo, s3 = make_service()
    repo.create.side_effect = RuntimeError("database unavailable")
    s3.delete_file.side_effect = RuntimeError("S3 unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.upload_image(
            product_id=uuid4(),
            data=b"content",
            filename="photo.png",
            content_type="image/png",
            content_hash="hash",
        )

    s3.delete_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_image_cleans_up_after_set_primary_failure():
    service, repo, s3 = make_service()
    repo.set_primary.side_effect = RuntimeError("primary update failed")

    with pytest.raises(RuntimeError, match="primary update failed"):
        await service.upload_image(
            product_id=uuid4(),
            data=b"content",
            filename="photo.png",
            content_type="image/png",
            content_hash="hash",
            is_primary=True,
        )

    s3.delete_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_images_uploads_each_file():
    service, repo, s3 = make_service()
    product_id = uuid4()
    uploads = [
        UploadFile(filename="one.png", file=BytesIO(b"one")),
        UploadFile(filename="two", file=BytesIO(b"two")),
    ]

    images = await service.add_images(
        product_id=product_id,
        images=uploads,
        content_hashes=["hash-one", "hash-two"],
    )

    assert len(images) == 2
    assert repo.create.await_count == 2
    assert s3.upload_file.await_count == 2
    assert all(image.product_id == product_id for image in images)


@pytest.mark.asyncio
async def test_add_images_rolls_back_prior_upload_when_later_upload_fails():
    service, repo, s3 = make_service()
    product_id = uuid4()
    uploads = [
        UploadFile(filename="one.png", file=BytesIO(b"one")),
        UploadFile(filename="two.png", file=BytesIO(b"two")),
    ]
    first_image = MagicMock(object_key="products/first.png", id=uuid4())
    repo.create.side_effect = [first_image, RuntimeError("database unavailable")]

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.add_images(
            product_id=product_id,
            images=uploads,
            content_hashes=["hash-one", "hash-two"],
        )

    repo.delete.assert_awaited_once()
    # upload_image removes the failed second object; add_images removes the first.
    assert s3.delete_file.await_count == 2


@pytest.mark.asyncio
async def test_add_images_logs_and_continues_if_rollback_cleanup_fails():
    service, repo, s3 = make_service()
    uploads = [
        UploadFile(filename="one.png", file=BytesIO(b"one")),
        UploadFile(filename="two.png", file=BytesIO(b"two")),
    ]
    first_image = MagicMock(object_key="products/first.png", id=uuid4())
    repo.create.side_effect = [first_image, RuntimeError("database unavailable")]
    repo.delete.side_effect = RuntimeError("delete failed")
    s3.delete_file.side_effect = [
        None,
        RuntimeError("cleanup failed"),
    ]

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.add_images(
            product_id=uuid4(),
            images=uploads,
            content_hashes=["hash-one", "hash-two"],
        )

    repo.delete.assert_awaited_once()
    assert s3.delete_file.await_count == 2


@pytest.mark.asyncio
async def test_add_images_rejects_different_number_of_hashes():
    service, repo, s3 = make_service()
    upload = UploadFile(filename="one.png", file=BytesIO(b"one"))

    with pytest.raises(ValueError):
        await service.add_images(
            product_id=uuid4(),
            images=[upload],
            content_hashes=[],
        )

    repo.create.assert_not_awaited()
    s3.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_images_delegates_to_repository():
    service, repo, _ = make_service()
    ids = [uuid4(), uuid4()]
    product_id = uuid4()
    expected = [MagicMock(), MagicMock()]
    repo.get_by_ids_and_product = AsyncMock(return_value=expected)

    assert await service.get_images(image_ids=ids, product_id=product_id) == expected
    repo.get_by_ids_and_product.assert_awaited_once_with(
        image_ids=ids,
        product_id=product_id,
    )


@pytest.mark.asyncio
async def test_delete_image_deletes_record_and_swallows_s3_failure():
    service, repo, s3 = make_service()
    image = ProductImage(
        id=uuid4(),
        product_id=uuid4(),
        object_key="products/image.png",
        is_primary=False,
    )
    repo.get_by_id_and_product = AsyncMock(return_value=image)
    s3.delete_file.side_effect = RuntimeError("S3 unavailable")

    await service.delete_image(image_id=image.id, product_id=image.product_id)

    repo.delete.assert_awaited_once_with(image)
    s3.delete_file.assert_awaited_once_with(object_key=image.object_key)


@pytest.mark.asyncio
async def test_delete_by_product_id_with_no_images_does_not_delete_objects():
    service, repo, s3 = make_service()
    repo.get_by_product_id = AsyncMock(return_value=[])

    await service.delete_by_product_id(uuid4())

    s3.delete_file.assert_not_awaited()
