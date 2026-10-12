from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.constants import ProductImageConstants
from app.exceptions.custom import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.models.category_model import Category
from app.models.product_image_model import ProductImage
from app.models.product_model import Product
from app.schemas.product_schema import ProductUpdate
from app.services.product_service import ProductService


def make_service(db=None, *, commit_error=None):
    db = db or MagicMock()
    if commit_error is not None:
        db.commit = AsyncMock(side_effect=commit_error)
    elif not isinstance(getattr(db, "commit", None), AsyncMock):
        db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()

    cache = MagicMock()
    cache.invalidate_product_list_cache = AsyncMock()
    cache.invalidate_product_cache = AsyncMock()
    cache.invalidate_category_list_cache = AsyncMock()
    cache.delete_keys = AsyncMock()

    arq_pool = MagicMock()
    arq_pool.enqueue_job = AsyncMock(return_value=MagicMock())

    redis = MagicMock()

    service = ProductService(
        db,
        s3_service=MagicMock(),
        arq_pool=arq_pool,
        cache=cache,
        redis=redis,
    )

    service.event_service = MagicMock()
    service.event_service.publish = AsyncMock()

    return service


@pytest.mark.asyncio
async def test_get_product_returns_product():
    service = make_service()
    product_id = uuid4()
    product = Product(id=product_id, name="iphone 15", sku="IPHONE-15")

    service.product_repo.get_by_id = AsyncMock(return_value=product)

    result = await service.get_product(product_id=product_id)

    assert result is product
    service.product_repo.get_by_id.assert_awaited_once_with(product_id=product_id)


@pytest.mark.asyncio
async def test_get_product_raises_not_found_when_product_does_not_exist():
    service = make_service()
    product_id = uuid4()

    service.product_repo.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_product(product_id=product_id)

    assert str(exc_info.value) == "Product not found"
    service.product_repo.get_by_id.assert_awaited_once_with(product_id=product_id)


@pytest.mark.asyncio
async def test_delete_product_raises_not_found_when_product_does_not_exist():
    service = make_service()
    product_id = uuid4()

    service.product_repo.get_by_id = AsyncMock(return_value=None)
    service.product_repo.delete = AsyncMock()

    with pytest.raises(NotFoundException) as exc_info:
        await service.delete_product(product_id=product_id, request_id="req-1")

    assert str(exc_info.value) == "Product not found"
    service.product_repo.get_by_id.assert_awaited_once_with(product_id=product_id)
    service.product_repo.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_product_does_not_delete_s3_objects_when_commit_fails():
    service = make_service(commit_error=RuntimeError("commit failed"))
    product_id = uuid4()
    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")
    product.images = [ProductImage(object_key="products/image.jpg")]

    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.product_repo.delete = AsyncMock()
    service.product_image_service.s3_service.delete_file = AsyncMock()

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.delete_product(product_id=product_id, request_id="req-1")

    service.product_image_service.s3_service.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_s3_continues_when_one_delete_fails():
    service = object.__new__(ProductService)
    service.product_image_service = MagicMock()
    service.product_image_service.s3_service.delete_file = AsyncMock(
        side_effect=[RuntimeError("S3 unavailable"), None]
    )

    await service._cleanup_s3(["products/image1.jpg", "products/image2.jpg"])

    assert service.product_image_service.s3_service.delete_file.await_count == 2


@pytest.mark.asyncio
async def test_update_product_raises_not_found_when_product_does_not_exist():
    service = make_service()
    product_id = uuid4()

    service.product_repo.get_by_id = AsyncMock(return_value=None)
    service.product_repo.update = AsyncMock()

    payload = ProductUpdate(name="Updated Product")

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_product(
            product_id=product_id,
            payload=payload,
            images=[],
            request_id="req-1",
        )

    assert str(exc_info.value) == "Product not found"
    service.product_repo.get_by_id.assert_awaited_once_with(product_id=product_id)
    service.product_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_product_updates_product():
    service = make_service()
    product_id = uuid4()

    product = Product(
        id=product_id,
        name="iPhone 15",
        price=Decimal("799.99"),
        sku="IPHONE-15",
    )
    updated_product = Product(
        id=product_id,
        name="iPhone 15 Pro",
        price=Decimal("999.99"),
        stock=15,
        sku="IPHONE-15",
    )

    service.product_repo.get_by_id = AsyncMock(
        side_effect=[product, updated_product],
    )
    service.product_repo.update = AsyncMock(return_value=updated_product)

    payload = ProductUpdate(
        name="iPhone 15 Pro",
        price=Decimal("999.99"),
        stock=15,
    )

    result = await service.update_product(
        product_id=product_id,
        payload=payload,
        images=[],
        request_id="req-1",
    )

    assert result is updated_product
    assert product.name == "iPhone 15 Pro"
    assert product.price == Decimal("999.99")
    assert product.stock == 15

    service.product_repo.update.assert_awaited_once_with(product=product)
    assert service.product_repo.get_by_id.await_count == 2


@pytest.mark.asyncio
async def test_update_product_rejects_total_images_above_limit():
    service = make_service()
    product_id = uuid4()
    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")
    product.images = [
        ProductImage(id=uuid4(), product_id=product_id)
        for _ in range(ProductImageConstants.MAX_IMAGES)
    ]
    new_image = MagicMock()
    new_image.filename = "new.png"
    new_image.content_type = "image/png"
    new_image.size = 100

    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.product_repo.update = AsyncMock()

    with pytest.raises(BadRequestException, match="Maximum 6 images are allowed"):
        await service.update_product(
            product_id=product_id,
            payload=ProductUpdate(name="Updated product"),
            images=[new_image],
            request_id="req-1",
        )

    service.product_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_product_raises_conflict_when_sku_already_exists():
    service = make_service()
    product_id = uuid4()
    existing_product_id = uuid4()

    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")
    existing_product = Product(
        id=existing_product_id, name="Samsung", sku="SAMSUNG-S23"
    )

    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.product_repo.get_by_sku = AsyncMock(return_value=existing_product)
    service.product_repo.update = AsyncMock()

    payload = ProductUpdate(sku="SAMSUNG-S23")

    with pytest.raises(ConflictException) as exc_info:
        await service.update_product(
            product_id=product_id,
            payload=payload,
            images=[],
            request_id="req-1",
        )

    assert str(exc_info.value) == "Product with this SKU already exists"
    service.product_repo.get_by_id.assert_awaited_once_with(product_id=product_id)
    service.product_repo.get_by_sku.assert_awaited_once_with(sku="SAMSUNG-S23")
    service.product_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_product_raises_not_found_when_category_does_not_exist():
    service = make_service()
    product_id = uuid4()

    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")

    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.category_repo.get_by_name = AsyncMock(return_value=None)
    service.product_repo.update = AsyncMock()

    payload = ProductUpdate(category_name="Non Existing Category")

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_product(
            product_id=product_id,
            payload=payload,
            images=[],
            request_id="req-1",
        )

    assert str(exc_info.value) == "Category not found"
    service.category_repo.get_by_name.assert_awaited_once_with(
        name="Non Existing Category"
    )
    service.product_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_product_updates_category():
    service = make_service()
    product_id = uuid4()
    category_id = uuid4()

    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")
    category = Category(id=category_id, name="Electronics")

    service.product_repo.get_by_id = AsyncMock(
        side_effect=[product, product],
    )
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.product_repo.update = AsyncMock(return_value=product)

    payload = ProductUpdate(category_name=" Electronics ")

    result = await service.update_product(
        product_id=product_id,
        payload=payload,
        images=[],
        request_id="req-1",
    )

    assert result is product
    assert product.category_id == category_id
    service.category_repo.get_by_name.assert_awaited_once_with(name="Electronics")
    service.product_repo.update.assert_awaited_once_with(product=product)
    service.product_repo.get_by_id.assert_awaited()


@pytest.mark.asyncio
async def test_update_product_publish_failure_does_not_raise_or_cleanup_staging():

    service = make_service()
    product_id = uuid4()

    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")

    service.product_repo.get_by_id = AsyncMock(
        side_effect=[product, product],
    )
    service.product_repo.update = AsyncMock(return_value=product)
    service.event_service.publish = AsyncMock(side_effect=RuntimeError("Redis down"))
    service._cleanup_s3 = AsyncMock()

    payload = ProductUpdate(name="iPhone 15 Pro")

    result = await service.update_product(
        product_id=product_id,
        payload=payload,
        images=[],
        request_id="req-1",
    )

    assert result is product
    service._cleanup_s3.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_product_enqueue_returning_none_does_not_raise():

    service = make_service()
    product_id = uuid4()

    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")

    service.product_repo.get_by_id = AsyncMock(
        side_effect=[product, product],
    )
    service.product_repo.update = AsyncMock(return_value=product)
    service.arq_pool.enqueue_job = AsyncMock(return_value=None)

    fake_image = MagicMock()
    fake_image.filename = "photo.jpg"
    fake_image.content_type = "image/jpeg"
    fake_image.size = 1024
    fake_image.read = AsyncMock(return_value=b"data")
    fake_image.seek = AsyncMock()

    service.product_image_service.s3_service.upload_file = AsyncMock()

    with patch("app.services.product_service.hashlib.sha256") as mock_sha256:
        mock_sha256.return_value.hexdigest.return_value = "a" * 64

        async def _fake_hash(images):
            return ["a" * 64]

        service._hash_product_images = _fake_hash

        result = await service.update_product(
            product_id=product_id,
            payload=ProductUpdate(name="Updated"),
            images=[fake_image],
            request_id="req-1",
        )

    assert result is product


@pytest.mark.asyncio
async def test_update_product_invalidates_category_cache_when_category_changes():
    service = make_service()
    product_id = uuid4()
    old_category_id = uuid4()
    new_category_id = uuid4()

    product = Product(
        id=product_id,
        name="iPhone 15",
        sku="IPHONE-15",
        category_id=old_category_id,
    )
    new_category = Category(id=new_category_id, name="Phones")

    service.product_repo.get_by_id = AsyncMock(
        side_effect=[product, product],
    )
    service.category_repo.get_by_name = AsyncMock(return_value=new_category)
    service.product_repo.update = AsyncMock(return_value=product)

    await service.update_product(
        product_id=product_id,
        payload=ProductUpdate(category_name="Phones"),
        images=[],
        request_id="req-1",
    )

    service.cache.invalidate_category_list_cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_product_does_not_invalidate_categorycache_when_name_only_change():
    service = make_service()
    product_id = uuid4()

    product = Product(id=product_id, name="iPhone 15", sku="IPHONE-15")

    service.product_repo.get_by_id = AsyncMock(
        side_effect=[product, product],
    )
    service.product_repo.update = AsyncMock(return_value=product)

    await service.update_product(
        product_id=product_id,
        payload=ProductUpdate(name="iPhone 15 Updated"),
        images=[],
        request_id="req-1",
    )

    service.cache.invalidate_category_list_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_products_returns_products_and_total():
    service = make_service()

    products = [
        Product(id=uuid4(), name="iPhone 15", sku="IPHONE-15"),
        Product(id=uuid4(), name="Samsung S23", sku="SAMSUNG-S23"),
    ]

    service.paginate = AsyncMock(return_value=(products, 2))

    result = await service.list_products()

    assert result == (products, 2)
    service.paginate.assert_awaited_once()


def test_sort_fields_matches_product_sort_field_enum():
    from app.core.constants import ProductSortField

    assert set(ProductSortField) == set(ProductService.SORT_FIELDS.keys()), (
        "ProductService.SORT_FIELDS keys must be "
        "exactly the set of ProductSortField members"
    )
