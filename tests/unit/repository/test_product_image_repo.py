from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.product_image_model import ProductImage
from app.repositories.product_image_repo import ProductImageRepository


@pytest.mark.asyncio
async def test_create_returns_image():
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    repo = ProductImageRepository(db)

    image = ProductImage(
        id=uuid4(),
        product_id=uuid4(),
        object_key="products/iphone.jpg",
        is_primary=False,
    )

    result = await repo.create(
        image=image,
    )

    assert result is image

    db.add.assert_called_once_with(image)
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(image)


@pytest.mark.asyncio
async def test_get_by_id_returns_image():
    db = MagicMock()

    image_id = uuid4()

    image = ProductImage(
        id=image_id,
        product_id=uuid4(),
        object_key="products/iphone.jpg",
        is_primary=False,
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = image

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_image = await repo.get_by_id(
        image_id=image_id,
    )

    assert returned_image is image

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_image_does_not_exist():
    db = MagicMock()

    image_id = uuid4()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_image = await repo.get_by_id(
        image_id=image_id,
    )

    assert returned_image is None

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_id_and_product_returns_image():
    db = MagicMock()

    image_id = uuid4()
    product_id = uuid4()

    image = ProductImage(
        id=image_id,
        product_id=product_id,
        object_key="products/iphone.jpg",
        is_primary=False,
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = image

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_image = await repo.get_by_id_and_product(
        image_id=image_id,
        product_id=product_id,
    )

    assert returned_image is image

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_id_and_product_returns_none_when_not_found():
    db = MagicMock()

    image_id = uuid4()
    product_id = uuid4()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_image = await repo.get_by_id_and_product(
        image_id=image_id,
        product_id=product_id,
    )

    assert returned_image is None

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_product_id_returns_images():
    db = MagicMock()

    product_id = uuid4()

    images = [
        ProductImage(
            id=uuid4(),
            product_id=product_id,
            object_key="products/1.jpg",
            is_primary=True,
        ),
        ProductImage(
            id=uuid4(),
            product_id=product_id,
            object_key="products/2.jpg",
            is_primary=False,
        ),
    ]

    scalars_result = MagicMock()
    scalars_result.all.return_value = images

    result = MagicMock()
    result.scalars.return_value = scalars_result

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_images = await repo.get_by_product_id(
        product_id=product_id,
    )

    assert returned_images == images

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_product_id_returns_empty_list():
    db = MagicMock()

    scalars_result = MagicMock()
    scalars_result.all.return_value = []

    result = MagicMock()
    result.scalars.return_value = scalars_result

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_images = await repo.get_by_product_id(
        product_id=uuid4(),
    )

    assert returned_images == []

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_primary_by_product_id_returns_image():
    db = MagicMock()

    product_id = uuid4()

    image = ProductImage(
        id=uuid4(),
        product_id=product_id,
        object_key="products/primary.jpg",
        is_primary=True,
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = image

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_image = await repo.get_primary_by_product_id(
        product_id=product_id,
    )

    assert returned_image is image

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_primary_by_product_id_returns_none():
    db = MagicMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(
        return_value=result,
    )

    repo = ProductImageRepository(db)

    returned_image = await repo.get_primary_by_product_id(
        product_id=uuid4(),
    )

    assert returned_image is None

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_unset_primary_executes_update():
    db = MagicMock()
    db.execute = AsyncMock()

    repo = ProductImageRepository(db)

    product_id = uuid4()

    result = await repo.unset_primary(
        product_id=product_id,
    )

    assert result is None

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_primary_unsets_previous_primary_and_sets_image():
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    repo = ProductImageRepository(db)

    image = ProductImage(
        id=uuid4(),
        product_id=uuid4(),
        object_key="products/primary.jpg",
        is_primary=False,
    )

    repo.unset_primary = AsyncMock()

    result = await repo.set_primary(
        image=image,
    )

    assert result is image

    assert image.is_primary is True

    repo.unset_primary.assert_awaited_once_with(
        image.product_id,
    )

    db.flush.assert_awaited_once()

    db.refresh.assert_awaited_once_with(image)


@pytest.mark.asyncio
async def test_delete_removes_image():
    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()

    repo = ProductImageRepository(db)

    image = ProductImage(
        id=uuid4(),
        product_id=uuid4(),
        object_key="products/iphone.jpg",
        is_primary=False,
    )

    result = await repo.delete(
        image=image,
    )

    assert result is None

    db.delete.assert_awaited_once_with(image)

    db.flush.assert_awaited_once()
