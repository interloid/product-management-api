from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.core.constants import ProductImageConstants, ProductStatusEnum
from app.exceptions.custom import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.models.category_model import Category
from app.models.product_image_model import ProductImage
from app.models.product_model import Product
from app.schemas.product_schema import ProductCreate, ProductUpdate
from app.services.product_service import ProductService


def make_service():
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    cache = MagicMock()
    cache.invalidate_product_list_cache = AsyncMock()
    cache.invalidate_product_cache = AsyncMock()
    cache.invalidate_category_list_cache = AsyncMock()
    arq_pool = MagicMock()
    arq_pool.enqueue_job = AsyncMock(return_value=MagicMock())
    service = ProductService(
        db,
        s3_service=MagicMock(),
        arq_pool=arq_pool,
        cache=cache,
        redis=MagicMock(),
    )
    service.product_repo.get_by_id = AsyncMock()
    service.product_repo.get_by_sku = AsyncMock()
    service.product_repo.create = AsyncMock(side_effect=lambda product: product)
    service.product_repo.update = AsyncMock(side_effect=lambda product: product)
    service.product_repo.delete = AsyncMock()
    service.category_repo.get_by_name = AsyncMock()
    service.product_image_service.get_images = AsyncMock(return_value=[])
    service.product_image_service.set_primary_image = AsyncMock()
    service.product_image_service.product_image_repo.delete_by_ids_and_product = (
        AsyncMock()
    )
    db.refresh = AsyncMock()
    service.event_service.publish = AsyncMock()
    service.product_image_service.s3_service.upload_file = AsyncMock()
    service.product_image_service.s3_service.delete_file = AsyncMock()
    return service


def make_upload(
    filename="photo.png",
    content_type="image/png",
    content=b"unique image bytes",
    size="auto",
):
    headers = {"content-type": content_type} if content_type else {}
    return UploadFile(
        filename=filename,
        file=BytesIO(content),
        size=len(content) if size == "auto" else size,
        headers=headers,
    )


def make_create_payload():
    return ProductCreate(
        name="Phone",
        sku="PHONE-1",
        category_name=" Electronics ",
        price=Decimal("10.00"),
        stock=2,
        status=ProductStatusEnum.ACTIVE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image", "message"),
    [
        (make_upload(filename=None), "Image filename is required"),
        (
            make_upload(content_type=None),
            "Content type could not be determined",
        ),
        (make_upload(content_type="text/plain"), "Unsupported image type"),
        (make_upload(size=None), "Could not determine size"),
        (
            make_upload(content=b"x" * (ProductImageConstants.MAX_FILE_SIZE + 1)),
            "exceeds the maximum size",
        ),
    ],
)
async def test_validate_product_images_rejects_invalid_images(image, message):
    service = make_service()

    with pytest.raises(BadRequestException, match=message):
        await service._validate_product_images([image])


@pytest.mark.asyncio
async def test_validate_product_images_rejects_too_many_images():
    service = make_service()
    images = [make_upload() for _ in range(ProductImageConstants.MAX_IMAGES + 1)]

    with pytest.raises(BadRequestException, match="Maximum 6 images are allowed"):
        await service._validate_product_images(images)


@pytest.mark.asyncio
async def test_validate_product_images_accepts_valid_image():
    service = make_service()

    await service._validate_product_images([make_upload()])


@pytest.mark.asyncio
async def test_hash_product_images_hashes_multiple_chunks_and_resets_file():
    service = make_service()
    content = b"x" * (1024 * 1024 + 3)
    upload = make_upload(content=content)

    hashes = await service._hash_product_images([upload])

    import hashlib

    assert hashes == [hashlib.sha256(content).hexdigest()]
    assert await upload.read() == content


@pytest.mark.asyncio
async def test_hash_product_images_rejects_duplicate_content():
    service = make_service()
    uploads = [make_upload(content=b"same"), make_upload(content=b"same")]

    with pytest.raises(ConflictException, match="Duplicate images are not allowed"):
        await service._hash_product_images(uploads)


@pytest.mark.asyncio
async def test_create_product_rejects_duplicate_sku():
    service = make_service()
    service.product_repo.get_by_sku = AsyncMock(return_value=MagicMock())

    with pytest.raises(ConflictException, match="Product with this SKU already exists"):
        await service.create_product(make_create_payload(), [], "request")

    service.category_repo.get_by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_product_rejects_unknown_category():
    service = make_service()
    service.product_repo.get_by_sku = AsyncMock(return_value=None)
    service.category_repo.get_by_name = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException, match="Category not found"):
        await service.create_product(make_create_payload(), [], "request")


@pytest.mark.asyncio
async def test_create_product_commits_and_enqueues_uploaded_images():
    service = make_service()
    category = Category(id=uuid4(), name="Electronics")
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    product.category_id = category.id
    service.product_repo.get_by_sku = AsyncMock(return_value=None)
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.product_repo.create = AsyncMock(return_value=product)

    result = await service.create_product(
        make_create_payload(),
        [make_upload()],
        "request",
    )

    assert result is product
    service.db.commit.assert_awaited_once()
    service.db.rollback.assert_not_awaited()
    service.product_image_service.s3_service.upload_file.assert_awaited_once()
    service.cache.invalidate_product_list_cache.assert_awaited_once()
    service.cache.invalidate_category_list_cache.assert_awaited_once()
    service.event_service.publish.assert_awaited_once()
    service.arq_pool.enqueue_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_product_continues_when_post_commit_side_effects_fail():
    service = make_service()
    category = Category(id=uuid4(), name="Electronics")
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    product.category_id = category.id
    service.product_repo.get_by_sku = AsyncMock(return_value=None)
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.product_repo.create = AsyncMock(return_value=product)
    service.cache.invalidate_product_list_cache.side_effect = RuntimeError("cache")
    service.cache.invalidate_category_list_cache.side_effect = RuntimeError("cache")
    service.event_service.publish.side_effect = RuntimeError("event")
    service.arq_pool.enqueue_job.side_effect = RuntimeError("queue")

    assert (
        await service.create_product(make_create_payload(), [], "request")
    ) is product
    service.product_image_service.s3_service.delete_file.assert_not_awaited()
    service.db.rollback.assert_not_awaited()
    service.db.commit.assert_awaited_once()
    service.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_product_converts_integrity_error_and_cleans_s3():
    service = make_service()
    category = Category(id=uuid4(), name="Electronics")
    service.product_repo.get_by_sku = AsyncMock(return_value=None)
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.product_repo.create = AsyncMock(
        side_effect=IntegrityError("insert", {}, RuntimeError("duplicate")),
    )

    with pytest.raises(ConflictException, match="Product already exists"):
        await service.create_product(
            make_create_payload(),
            [make_upload()],
            "request",
        )

    service.db.rollback.assert_awaited_once()
    service.product_image_service.s3_service.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_product_rolls_back_and_cleans_staging_after_upload_failure():
    service = make_service()
    category = Category(id=uuid4(), name="Electronics")
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_sku = AsyncMock(return_value=None)
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.product_repo.create = AsyncMock(return_value=product)
    service.product_image_service.s3_service.upload_file.side_effect = RuntimeError(
        "upload failed",
    )

    with pytest.raises(RuntimeError, match="upload failed"):
        await service.create_product(
            make_create_payload(),
            [make_upload()],
            "request",
        )

    service.db.rollback.assert_awaited_once()
    service.product_image_service.s3_service.delete_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_product_rolls_back_when_upload_content_type_is_unsupported():
    service = make_service()
    category = Category(id=uuid4(), name="Electronics")
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_sku = AsyncMock(return_value=None)
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.product_repo.create = AsyncMock(return_value=product)
    service._validate_product_images = AsyncMock()

    with pytest.raises(BadRequestException, match="content type is required"):
        await service.create_product(
            make_create_payload(),
            [make_upload(content_type=None)],
            "request",
        )

    service.db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_product_rejects_unmapped_image_extension():
    service = make_service()
    category = Category(id=uuid4(), name="Electronics")
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_sku = AsyncMock(return_value=None)
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.product_repo.create = AsyncMock(return_value=product)
    service._validate_product_images = AsyncMock()

    with pytest.raises(BadRequestException, match="Unsupported image type"):
        await service.create_product(
            make_create_payload(),
            [make_upload(content_type="image/gif")],
            "request",
        )

    service.db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_product_rejects_unknown_removed_image():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(return_value=product)

    with pytest.raises(NotFoundException, match="Product image not found"):
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[],
            request_id="request",
            removed_image_ids=[uuid4()],
        )


@pytest.mark.asyncio
async def test_update_product_rejects_primary_image_not_owned_by_product():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(return_value=product)

    with pytest.raises(NotFoundException, match="Primary image not found"):
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[],
            request_id="request",
            primary_image_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_update_product_rejects_primary_image_marked_for_removal():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    image = ProductImage(
        id=uuid4(),
        product_id=product.id,
        object_key="products/image.png",
        is_primary=True,
    )
    product.images = [image]
    service.product_repo.get_by_id = AsyncMock(return_value=product)

    with pytest.raises(BadRequestException, match="cannot also be removed"):
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[],
            request_id="request",
            removed_image_ids=[image.id],
            primary_image_id=image.id,
        )


@pytest.mark.asyncio
async def test_update_product_rejects_duplicate_of_retained_image():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    product.images = [
        ProductImage(
            id=uuid4(),
            product_id=product.id,
            object_key="products/image.png",
            content_hash="same-hash",
            is_primary=True,
        ),
    ]
    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service._hash_product_images = AsyncMock(return_value=["same-hash"])

    with pytest.raises(ConflictException, match="Duplicate images are not allowed"):
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[make_upload()],
            request_id="request",
        )


@pytest.mark.asyncio
async def test_update_product_does_not_conflict_when_sku_is_unchanged():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(side_effect=[product, product])
    service.product_repo.update = AsyncMock(return_value=product)

    await service.update_product(
        product_id=product.id,
        payload=ProductUpdate(sku="PHONE-1"),
        images=[],
        request_id="request",
    )

    service.product_repo.get_by_sku.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_product_allows_sku_used_by_same_product():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(side_effect=[product, product])
    service.product_repo.get_by_sku = AsyncMock(return_value=product)
    service.product_repo.update = AsyncMock(return_value=product)

    await service.update_product(
        product_id=product.id,
        payload=ProductUpdate(sku="PHONE-2"),
        images=[],
        request_id="request",
    )

    service.product_repo.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_product_removes_images_and_promotes_retained_image():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    primary = ProductImage(
        id=uuid4(),
        product_id=product.id,
        object_key="products/primary.png",
        is_primary=True,
    )
    retained = ProductImage(
        id=uuid4(),
        product_id=product.id,
        object_key="products/retained.png",
        is_primary=False,
    )
    product.images = [primary, retained]
    service.product_repo.get_by_id = AsyncMock(side_effect=[product, product])
    service.product_repo.update = AsyncMock(return_value=product)
    service.product_image_service.get_images = AsyncMock(return_value=[primary])
    service.product_image_service.set_primary_image = AsyncMock(return_value=retained)

    result = await service.update_product(
        product_id=product.id,
        payload=ProductUpdate(name="Updated"),
        images=[],
        request_id="request",
        removed_image_ids=[primary.id],
    )

    assert result is product
    service.product_image_service.product_image_repo.delete_by_ids_and_product.assert_awaited_once()
    service.product_image_service.set_primary_image.assert_awaited_once_with(
        image_id=retained.id,
        product_id=product.id,
    )
    service.arq_pool.enqueue_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_product_replaces_images_and_continues_on_post_commit_errors():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(side_effect=[product, product])
    service.product_repo.update = AsyncMock(return_value=product)
    service.event_service.publish.side_effect = RuntimeError("event failed")
    service._enqueue_s3_cleanup = AsyncMock(side_effect=RuntimeError("queue failed"))
    service.arq_pool.enqueue_job.side_effect = RuntimeError("image queue failed")
    upload = make_upload()
    service._hash_product_images = AsyncMock(return_value=["new-hash"])
    service._validate_product_images = AsyncMock()

    result = await service.update_product(
        product_id=product.id,
        payload=ProductUpdate(name="Updated"),
        images=[upload],
        request_id="request",
    )

    assert result is product
    service.product_image_service.s3_service.delete_file.assert_not_awaited()
    service.db.rollback.assert_not_awaited()
    service.db.commit.assert_awaited_once()
    service.db.refresh.assert_awaited_once_with(product, ["images", "category"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "message"),
    [
        (None, "content type is required"),
        ("image/gif", "Unsupported image type"),
    ],
)
async def test_update_product_rejects_unusable_new_image(content_type, message):
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.product_repo.update = AsyncMock(return_value=product)
    service._validate_product_images = AsyncMock()
    service._hash_product_images = AsyncMock(return_value=["new-hash"])

    with pytest.raises(BadRequestException, match=message):
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[make_upload(content_type=content_type)],
            request_id="request",
        )

    service.db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_product_sets_requested_primary_image():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    image = ProductImage(
        id=uuid4(),
        product_id=product.id,
        object_key="products/image.png",
        is_primary=False,
    )
    product.images = [image]
    service.product_repo.get_by_id = AsyncMock(side_effect=[product, product])
    service.product_repo.update = AsyncMock(return_value=product)

    result = await service.update_product(
        product_id=product.id,
        payload=ProductUpdate(name="Updated"),
        images=[],
        request_id="request",
        primary_image_id=image.id,
    )

    assert result is product
    service.product_image_service.set_primary_image.assert_awaited_once_with(
        image_id=image.id,
        product_id=product.id,
    )


@pytest.mark.asyncio
async def test_update_product_logs_when_image_job_returns_none():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")

    service.product_repo.get_by_id = AsyncMock(side_effect=[product, product])
    service.product_repo.update = AsyncMock(return_value=product)
    service._hash_product_images = AsyncMock(return_value=["new-hash"])
    service.arq_pool.enqueue_job = AsyncMock(return_value=None)

    with patch("app.services.product_service.logger.error") as mock_logger_error:
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[make_upload()],
            request_id="request",
        )

    mock_logger_error.assert_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("in_stock", [True, False])
async def test_list_products_builds_all_optional_filters(in_stock):
    service = make_service()
    products = [Product(id=uuid4(), name="Phone", sku="PHONE-1")]
    service.paginate = AsyncMock(return_value=(products, 1))

    result = await service.list_products(
        search=" Phone ",
        category_name="Electronics",
        status=ProductStatusEnum.ACTIVE,
        min_price=Decimal("1.00"),
        max_price=Decimal("50.00"),
        in_stock=in_stock,
        sort_by="name",
        sort_order=" ASC ",
    )

    assert result == (products, 1)
    service.paginate.assert_awaited_once()
    stmt = service.paginate.await_args.args[0]
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled)

    assert "products.price >= 1.00" in sql
    assert "products.price <= 50.00" in sql
    assert "products.status =" in sql

    if in_stock:
        assert "products.stock > 0" in sql
    else:
        assert "products.stock = 0" in sql


@pytest.mark.asyncio
async def test_update_product_returns_not_found_if_product_vanishes_after_update():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(side_effect=[product, None])
    service.product_repo.update = AsyncMock(return_value=product)

    with pytest.raises(NotFoundException, match="Product not found"):
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[],
            request_id="request",
        )


@pytest.mark.asyncio
async def test_update_product_converts_integrity_error():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.product_repo.update = AsyncMock(
        side_effect=IntegrityError("update", {}, RuntimeError("duplicate")),
    )

    with pytest.raises(ConflictException, match="conflicting resource"):
        await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(name="Updated"),
            images=[],
            request_id="request",
        )

    service.db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_product_logs_cache_invalidation_error_after_category_change():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1", category_id=uuid4())
    category = Category(id=uuid4(), name="New category")

    service.product_repo.get_by_id = AsyncMock(side_effect=[product, product])
    service.product_repo.update = AsyncMock(return_value=product)
    service.category_repo.get_by_name = AsyncMock(return_value=category)
    service.cache.invalidate_category_list_cache = AsyncMock(
        side_effect=RuntimeError("cache")
    )

    with patch("app.services.product_service.logger.error") as mock_logger_error:
        result = await service.update_product(
            product_id=product.id,
            payload=ProductUpdate(category_name="New category"),
            images=[],
            request_id="request",
        )

    assert result is product
    assert product.category_id == category.id
    service.cache.invalidate_category_list_cache.assert_awaited()
    mock_logger_error.assert_called()


@pytest.mark.asyncio
async def test_delete_product_deletes_record_invalidates_caches_and_enqueues_cleanup():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    product.images = [
        ProductImage(
            id=uuid4(),
            product_id=product.id,
            object_key="products/photo.png",
        ),
    ]
    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.product_repo.delete = AsyncMock()

    await service.delete_product(product.id, "request")

    service.product_repo.delete.assert_awaited_once_with(product=product)
    service.db.commit.assert_awaited_once()
    service.cache.invalidate_product_list_cache.assert_awaited_once()
    service.cache.invalidate_category_list_cache.assert_awaited_once()
    service.cache.invalidate_product_cache.assert_awaited_once_with(id=product.id)
    service.event_service.publish.assert_awaited_once()
    service.arq_pool.enqueue_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_product_logs_s3_cleanup_queue_failure():
    service = make_service()
    product = Product(id=uuid4(), name="Phone", sku="PHONE-1")
    product.images = []

    service.product_repo.get_by_id = AsyncMock(return_value=product)
    service.product_repo.delete = AsyncMock()
    service._enqueue_s3_cleanup = AsyncMock(side_effect=RuntimeError("queue"))

    with patch(
        "app.services.product_service.logger.exception"
    ) as mock_logger_exception:
        await service.delete_product(product.id, "request")

    mock_logger_exception.assert_called()
    service.db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_s3_cleanup_skips_empty_list():
    service = make_service()

    await service._enqueue_s3_cleanup([])

    service.arq_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_s3_cleanup_queues_keys():
    service = make_service()

    await service._enqueue_s3_cleanup(["products/image.png"])

    service.arq_pool.enqueue_job.assert_awaited_once_with(
        "delete_s3_jobs",
        ["products/image.png"],
        _expires=86_400,
    )
