from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.product_model import Product
from app.repositories.product_repo import ProductRepository


@pytest.mark.asyncio
async def test_get_by_id_returns_product():
    db = MagicMock()
    product_id = uuid4()

    product = Product(
        id=product_id,
        name="iPhone 15",
        sku="IPHONE-15",
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = product

    db.execute = AsyncMock(return_value=result)

    repo = ProductRepository(db)

    returned_product = await repo.get_by_id(
        product_id=product_id,
    )

    assert returned_product is product

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_product_does_not_exist():
    db = MagicMock()
    product_id = uuid4()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(return_value=result)

    repo = ProductRepository(db)

    returned_product = await repo.get_by_id(
        product_id=product_id,
    )

    assert returned_product is None

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_sku_returns_product():
    db = MagicMock()

    product = Product(
        id=uuid4(),
        name="iPhone 15",
        sku="IPHONE-15",
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = product

    db.execute = AsyncMock(return_value=result)

    repo = ProductRepository(db)

    returned_product = await repo.get_by_sku(
        sku="IPHONE-15",
    )

    assert returned_product is product

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_sku_returns_none_when_product_does_not_exist():
    db = MagicMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(return_value=result)

    repo = ProductRepository(db)

    returned_product = await repo.get_by_sku(
        sku="UNKNOWN-SKU",
    )

    assert returned_product is None

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_returns_created_product():
    db = MagicMock()
    db.flush = AsyncMock()

    repo = ProductRepository(db)

    product = Product(
        id=uuid4(),
        name="iPhone 15",
        sku="IPHONE-15",
    )

    repo.get_by_id = AsyncMock(
        return_value=product,
    )

    result = await repo.create(
        product=product,
    )

    assert result is product

    db.add.assert_called_once_with(product)

    db.flush.assert_awaited_once()

    repo.get_by_id.assert_awaited_once_with(
        product_id=product.id,
    )


@pytest.mark.asyncio
async def test_create_raises_runtime_error_when_product_cannot_be_retrieved():
    db = MagicMock()
    db.flush = AsyncMock()

    repo = ProductRepository(db)

    product = Product(
        id=uuid4(),
        name="iPhone 15",
        sku="IPHONE-15",
    )

    repo.get_by_id = AsyncMock(
        return_value=None,
    )

    with pytest.raises(
        RuntimeError,
        match="Created product could not be retrieved",
    ):
        await repo.create(
            product=product,
        )

    db.add.assert_called_once_with(product)

    db.flush.assert_awaited_once()

    repo.get_by_id.assert_awaited_once_with(
        product_id=product.id,
    )


@pytest.mark.asyncio
async def test_update_returns_updated_product():
    db = MagicMock()
    db.flush = AsyncMock()

    repo = ProductRepository(db)

    product_id = uuid4()

    product = Product(
        id=product_id,
        name="iPhone 15",
        sku="IPHONE-15",
    )

    updated_product = Product(
        id=product_id,
        name="iPhone 15 Pro",
        sku="IPHONE-15",
    )

    repo.get_by_id = AsyncMock(
        return_value=updated_product,
    )

    result = await repo.update(
        product=product,
    )

    assert result is updated_product

    db.flush.assert_awaited_once()

    repo.get_by_id.assert_awaited_once_with(
        product_id=product_id,
    )


@pytest.mark.asyncio
async def test_update_raises_runtime_error_when_product_cannot_be_retrieved():
    db = MagicMock()
    db.flush = AsyncMock()

    repo = ProductRepository(db)

    product = Product(
        id=uuid4(),
        name="iPhone 15",
        sku="IPHONE-15",
    )

    repo.get_by_id = AsyncMock(
        return_value=None,
    )

    with pytest.raises(
        RuntimeError,
        match="Updated product could not be retrieved",
    ):
        await repo.update(
            product=product,
        )

    db.flush.assert_awaited_once()

    repo.get_by_id.assert_awaited_once_with(
        product_id=product.id,
    )


@pytest.mark.asyncio
async def test_delete_removes_product():
    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()

    repo = ProductRepository(db)

    product = Product(
        id=uuid4(),
        name="iPhone 15",
        sku="IPHONE-15",
    )

    result = await repo.delete(
        product=product,
    )

    assert result is None

    db.delete.assert_awaited_once_with(product)

    db.flush.assert_awaited_once()
