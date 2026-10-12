from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.exceptions.custom import NotFoundException
from app.models.product_image_model import ProductImage
from app.services.product_image_service import ProductImageService


@pytest.mark.asyncio
async def test_upload_image_creates_product_image():
    product_id = uuid4()
    data = b"fake image"

    repo = MagicMock()
    s3_service = MagicMock()

    s3_service.upload_file = AsyncMock()

    created_image = MagicMock()

    repo.create = AsyncMock(
        return_value=created_image,
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.upload_image(
        product_id=product_id,
        data=data,
        filename="iphone.jpg",
        content_type="image/jpeg",
    )

    assert result is created_image

    s3_service.upload_file.assert_awaited_once()

    repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_image_generates_object_key_with_extension():
    product_id = uuid4()
    data = b"fake image"

    repo = MagicMock()
    s3_service = MagicMock()

    s3_service.upload_file = AsyncMock()

    repo.create = AsyncMock(
        side_effect=lambda image: image,
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.upload_image(
        product_id=product_id,
        data=data,
        filename="iphone.jpg",
        content_type="image/jpeg",
    )

    object_key = result.object_key

    assert object_key.startswith(
        f"products/{product_id}/images/",
    )

    assert object_key.endswith(".jpg")

    s3_service.upload_file.assert_awaited_once_with(
        data=data,
        object_key=object_key,
        content_type="image/jpeg",
    )


@pytest.mark.asyncio
async def test_upload_image_generates_object_key_without_extension():
    product_id = uuid4()
    data = b"fake image"

    repo = MagicMock()
    s3_service = MagicMock()

    s3_service.upload_file = AsyncMock()

    repo.create = AsyncMock(
        side_effect=lambda image: image,
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.upload_image(
        product_id=product_id,
        data=data,
        filename="iphone",
        content_type="image/jpeg",
    )

    assert result.object_key.startswith(
        f"products/{product_id}/images/",
    )

    assert not result.object_key.endswith(".")


@pytest.mark.asyncio
async def test_get_image_returns_image():
    product_id = uuid4()
    image_id = uuid4()

    image = ProductImage(
        id=image_id,
        product_id=product_id,
        object_key="products/image.jpg",
        is_primary=False,
    )

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_id_and_product = AsyncMock(
        return_value=image,
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.get_image(
        image_id=image_id,
        product_id=product_id,
    )

    assert result is image

    repo.get_by_id_and_product.assert_awaited_once_with(
        image_id=image_id,
        product_id=product_id,
    )


@pytest.mark.asyncio
async def test_get_image_raises_not_found_when_image_does_not_exist():
    product_id = uuid4()
    image_id = uuid4()

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_id_and_product = AsyncMock(
        return_value=None,
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_image(
            image_id=image_id,
            product_id=product_id,
        )

    assert str(exc_info.value) == "Product image not found"

    repo.get_by_id_and_product.assert_awaited_once_with(
        image_id=image_id,
        product_id=product_id,
    )


@pytest.mark.asyncio
async def test_get_product_images_returns_images():
    product_id = uuid4()

    images = [
        ProductImage(
            id=uuid4(),
            product_id=product_id,
            object_key="products/image1.jpg",
            is_primary=True,
        ),
        ProductImage(
            id=uuid4(),
            product_id=product_id,
            object_key="products/image2.jpg",
            is_primary=False,
        ),
    ]

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_product_id = AsyncMock(
        return_value=images,
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.get_product_images(
        product_id=product_id,
    )

    assert result == images

    repo.get_by_product_id.assert_awaited_once_with(
        product_id=product_id,
    )


@pytest.mark.asyncio
async def test_get_product_images_returns_empty_list_when_no_images():
    product_id = uuid4()

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_product_id = AsyncMock(
        return_value=[],
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.get_product_images(
        product_id=product_id,
    )

    assert result == []

    repo.get_by_product_id.assert_awaited_once_with(
        product_id=product_id,
    )


@pytest.mark.asyncio
async def test_set_primary_image_sets_non_primary_image():
    product_id = uuid4()
    image_id = uuid4()

    image = ProductImage(
        id=image_id,
        product_id=product_id,
        object_key="products/image.jpg",
        is_primary=False,
    )

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_id_and_product = AsyncMock(
        return_value=image,
    )

    repo.set_primary = AsyncMock(
        return_value=image,
    )

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.set_primary_image(
        image_id=image_id,
        product_id=product_id,
    )

    assert result is image

    repo.get_by_id_and_product.assert_awaited_once_with(
        image_id=image_id,
        product_id=product_id,
    )

    repo.set_primary.assert_awaited_once_with(image)


@pytest.mark.asyncio
async def test_set_primary_image_returns_image_when_already_primary():
    product_id = uuid4()
    image_id = uuid4()

    image = ProductImage(
        id=image_id,
        product_id=product_id,
        object_key="products/image.jpg",
        is_primary=True,
    )

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_id_and_product = AsyncMock(
        return_value=image,
    )

    repo.set_primary = AsyncMock()

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    result = await service.set_primary_image(
        image_id=image_id,
        product_id=product_id,
    )

    assert result is image

    repo.get_by_id_and_product.assert_awaited_once_with(
        image_id=image_id,
        product_id=product_id,
    )

    repo.set_primary.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_primary_image_raises_not_found_when_image_does_not_exist():
    product_id = uuid4()
    image_id = uuid4()

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_id_and_product = AsyncMock(
        return_value=None,
    )

    repo.set_primary = AsyncMock()

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.set_primary_image(
            image_id=image_id,
            product_id=product_id,
        )

    assert str(exc_info.value) == "Product image not found"

    repo.get_by_id_and_product.assert_awaited_once_with(
        image_id=image_id,
        product_id=product_id,
    )

    repo.set_primary.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_by_product_id_successfully():
    product_id = uuid4()

    image1 = MagicMock()
    image1.object_key = "products/image1.jpg"

    image2 = MagicMock()
    image2.object_key = "products/image2.jpg"

    product_image_repo = MagicMock()
    product_image_repo.get_by_product_id = AsyncMock(return_value=[image1, image2])

    s3_service = MagicMock()
    s3_service.delete_file = AsyncMock()

    service = ProductImageService(
        product_image_repo=product_image_repo,
        s3_service=s3_service,
    )

    await service.delete_by_product_id(
        product_id=product_id,
    )

    product_image_repo.get_by_product_id.assert_awaited_once_with(
        product_id=product_id,
    )

    assert s3_service.delete_file.await_count == 2

    s3_service.delete_file.assert_any_await(
        object_key="products/image1.jpg",
    )

    s3_service.delete_file.assert_any_await(
        object_key="products/image2.jpg",
    )


@pytest.mark.asyncio
async def test_delete_by_product_id_continues_when_one_s3_delete_fails():
    product_id = uuid4()
    images = [
        MagicMock(object_key="products/image1.jpg"),
        MagicMock(object_key="products/image2.jpg"),
    ]
    product_image_repo = MagicMock()
    product_image_repo.get_by_product_id = AsyncMock(return_value=images)
    s3_service = MagicMock()
    s3_service.delete_file = AsyncMock(
        side_effect=[RuntimeError("S3 unavailable"), None]
    )
    service = ProductImageService(
        product_image_repo=product_image_repo,
        s3_service=s3_service,
    )

    await service.delete_by_product_id(product_id=product_id)

    assert s3_service.delete_file.await_count == 2


@pytest.mark.asyncio
async def test_delete_image_raises_not_found_when_image_does_not_exist():
    product_id = uuid4()
    image_id = uuid4()

    repo = MagicMock()
    s3_service = MagicMock()

    repo.get_by_id_and_product = AsyncMock(
        return_value=None,
    )

    repo.delete = AsyncMock()
    s3_service.delete_file = AsyncMock()

    service = ProductImageService(
        product_image_repo=repo,
        s3_service=s3_service,
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.delete_image(
            image_id=image_id,
            product_id=product_id,
        )

    assert str(exc_info.value) == "Product image not found"

    repo.get_by_id_and_product.assert_awaited_once_with(
        image_id=image_id,
        product_id=product_id,
    )

    s3_service.delete_file.assert_not_awaited()
    repo.delete.assert_not_awaited()
