from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.dependencies import (
    get_cache_service,
    get_current_user,
    get_product_service,
)
from app.core.constants import RoleEnum
from app.core.rate_limiter import (
    enforce_read_rate_limit,
    enforce_write_rate_limit,
)
from app.core.s3 import get_s3_service
from app.exceptions.custom import NotFoundException
from app.main import app


@pytest.fixture
def product_router_dependencies():
    user = MagicMock()
    user.role = RoleEnum.ADMIN

    service = MagicMock()
    service.create_product = AsyncMock()

    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=None)
    cache.set_json = AsyncMock()
    cache.add_to_set = AsyncMock()

    s3_service = MagicMock()
    s3_service.generate_cloudfront_urls = AsyncMock(return_value={})

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_product_service] = lambda: service
    app.dependency_overrides[get_cache_service] = lambda: cache
    app.dependency_overrides[get_s3_service] = lambda: s3_service
    app.dependency_overrides[enforce_read_rate_limit] = lambda: None
    app.dependency_overrides[enforce_write_rate_limit] = lambda: None

    yield {
        "user": user,
        "service": service,
        "cache": cache,
        "s3_service": s3_service,
    }

    for dependency in [
        get_current_user,
        get_product_service,
        get_cache_service,
        get_s3_service,
        enforce_read_rate_limit,
        enforce_write_rate_limit,
    ]:
        app.dependency_overrides.pop(dependency, None)


@pytest.mark.asyncio
async def test_list_products_returns_paginated_response(client):

    app_user = MagicMock()
    app_user.role = RoleEnum.VIEWER

    product_service = MagicMock()
    product_service.list_products = AsyncMock(return_value=([], 0))
    product_service.calculate_total_pages = MagicMock(return_value=0)

    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=None)
    cache.set_json = AsyncMock()
    cache.add_to_set = AsyncMock()

    s3_service = MagicMock()
    s3_service.generate_cloudfront_urls = AsyncMock(return_value={})

    async def override_current_user():
        return app_user

    async def override_product_service():
        return product_service

    async def override_cache_service():
        return cache

    async def override_s3_service():
        return s3_service

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_product_service] = override_product_service
    app.dependency_overrides[get_cache_service] = override_cache_service
    app.dependency_overrides[get_s3_service] = override_s3_service

    try:
        response = await client.get("/api/v1/products")

        assert response.status_code == 200

        body = response.json()
        assert body["data"] == []
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["page_size"] == 10
        assert body["pagination"]["total"] == 0
        assert body["pagination"]["total_pages"] == 0

        product_service.list_products.assert_awaited_once()
        cache.get_json.assert_awaited_once()
        cache.set_json.assert_awaited_once()
        cache.add_to_set.assert_awaited_once()

    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_product_service, None)
        app.dependency_overrides.pop(get_cache_service, None)
        app.dependency_overrides.pop(get_s3_service, None)


@pytest.mark.asyncio
async def test_list_products_returns_cached_data(client):
    app_user = MagicMock()
    app_user.role = RoleEnum.VIEWER

    cached_data = {
        "message": "Products retrieved successfully",
        "data": [],
        "pagination": {
            "page": 1,
            "page_size": 10,
            "total": 0,
            "total_pages": 0,
        },
    }

    product_service = MagicMock()
    product_service.list_products = AsyncMock()
    product_service.calculate_total_pages = MagicMock()

    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=cached_data)

    async def override_current_user():
        return app_user

    async def override_product_service():
        return product_service

    async def override_cache_service():
        return cache

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_product_service] = override_product_service
    app.dependency_overrides[get_cache_service] = override_cache_service

    try:
        response = await client.get("/api/v1/products")

        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.json()["pagination"]["total"] == 0

        product_service.list_products.assert_not_awaited()
        product_service.calculate_total_pages.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_product_service, None)
        app.dependency_overrides.pop(get_cache_service, None)


@pytest.mark.asyncio
async def test_create_product_success(client, product_router_dependencies):
    service = product_router_dependencies["service"]

    response = await client.post(
        "/api/v1/products",
        data={
            "name": "Integration Test Product",
            "sku": "INT-001",
            "category_name": "Home",
            "price": "99.99",
            "stock": "10",
            "status": "active",
            "description": "Created during integration testing",
        },
    )

    assert response.status_code == 202
    assert response.json()["message"] == "Product created successfully"

    service.create_product.assert_awaited_once()

    call_kwargs = service.create_product.await_args.kwargs
    assert call_kwargs["payload"].name == "Integration Test Product"
    assert call_kwargs["payload"].sku == "INT-001"
    assert call_kwargs["payload"].price == Decimal("99.99")
    assert call_kwargs["payload"].stock == 10
    assert call_kwargs["images"] == []
    assert call_kwargs["request_id"]


@pytest.mark.asyncio
async def test_update_product_success(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    product_id = uuid4()

    service.update_product = AsyncMock(
        return_value=SimpleNamespace(
            id=product_id,
            name="Updated Product",
            sku="UPD-001",
            category=SimpleNamespace(name="Home"),
            price=Decimal("149.99"),
            stock=20,
            status="active",
            description="Updated description",
            images=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    response = await client.patch(
        f"/api/v1/products/{product_id}",
        data={
            "name": "Updated Product",
            "price": "149.99",
            "stock": "20",
        },
    )

    assert response.status_code == 200
    service.update_product.assert_awaited_once()

    call_kwargs = service.update_product.await_args.kwargs
    assert call_kwargs["payload"].name == "Updated Product"
    assert call_kwargs["payload"].price == Decimal("149.99")
    assert call_kwargs["payload"].stock == 20


@pytest.mark.asyncio
async def test_delete_product_success(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]
    product_id = uuid4()

    service.delete_product = AsyncMock(return_value=None)

    response = await client.delete(f"/api/v1/products/{product_id}")

    assert response.status_code == 204
    assert response.content == b""

    service.delete_product.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_product_not_found(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]
    product_id = uuid4()

    service.get_product = AsyncMock(
        side_effect=NotFoundException(message="Product not found")
    )

    response = await client.get(f"/api/v1/products/{product_id}")

    assert response.status_code == 404
    service.get_product.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_product_invalid_uuid(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    response = await client.delete("/api/v1/products/not-a-valid-uuid")

    assert response.status_code == 422
    service.delete_product.assert_not_called()


@pytest.mark.asyncio
async def test_update_product_invalid_removed_image_uuid(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]
    product_id = uuid4()

    response = await client.patch(
        f"/api/v1/products/{product_id}",
        data={
            "removed_image_ids": '["not-a-valid-uuid"]',
        },
    )

    assert response.status_code == 422
    service.update_product.assert_not_called()


@pytest.mark.asyncio
async def test_list_products_passes_filters_to_service(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]
    cache = product_router_dependencies["cache"]

    service.list_products = AsyncMock(return_value=([], 0))
    service.calculate_total_pages = MagicMock(return_value=0)
    cache.get_json.return_value = None

    response = await client.get(
        "/api/v1/products",
        params={
            "search": "laptop",
            "category_name": "Electronics",
            "status": "active",
            "min_price": "100",
            "max_price": "1000",
            "in_stock": "true",
            "sort": "price",
            "order": "asc",
            "page": "2",
            "page_size": "5",
        },
    )

    assert response.status_code == 200
    service.list_products.assert_awaited_once()

    kwargs = service.list_products.await_args.kwargs

    assert kwargs["search"] == "laptop"
    assert kwargs["category_name"] == "Electronics"
    assert kwargs["min_price"] == Decimal("100")
    assert kwargs["max_price"] == Decimal("1000")
    assert kwargs["in_stock"] is True
    assert kwargs["page"] == 2
    assert kwargs["page_size"] == 5


@pytest.mark.asyncio
async def test_get_product_returns_cached_data(
    client,
    product_router_dependencies,
):
    cache = product_router_dependencies["cache"]
    service = product_router_dependencies["service"]

    product_id = str(uuid4())

    cached_data = {
        "message": "Product retrieved successfully",
        "data": {
            "id": product_id,
            "name": "Cached Product",
            "sku": "CACHE-001",
            "category_name": "Electronics",
            "price": "99.99",
            "stock": 10,
            "status": "active",
            "description": "Cached product",
            "images": [],
            "created_at": "2026-01-01T10:00:00Z",
            "updated_at": "2026-01-01T10:00:00Z",
        },
    }

    cache.get_json.return_value = cached_data

    response = await client.get(f"/api/v1/products/{product_id}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == product_id
    assert response.json()["data"]["name"] == "Cached Product"
    assert response.headers.get("etag") is not None

    service.get_product.assert_not_called()


@pytest.mark.asyncio
async def test_list_products_rejects_invalid_page(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    response = await client.get(
        "/api/v1/products",
        params={"page": 0},
    )

    assert response.status_code == 422
    service.list_products.assert_not_called()


@pytest.mark.asyncio
async def test_list_products_rejects_invalid_price_range(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    response = await client.get(
        "/api/v1/products",
        params={
            "min_price": "1000",
            "max_price": "100",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Minimum price cannot be greater than maximum price"
    )

    service.list_products.assert_not_called()


@pytest.mark.asyncio
async def test_get_product_rejects_invalid_uuid(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    response = await client.get("/api/v1/products/not-a-valid-uuid")

    assert response.status_code == 422
    service.get_product.assert_not_called()


@pytest.mark.asyncio
async def test_list_products_rejects_page_size_above_max(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    response = await client.get(
        "/api/v1/products",
        params={"page_size": 101},
    )

    assert response.status_code == 422
    service.list_products.assert_not_called()


@pytest.mark.asyncio
async def test_list_products_rejects_invalid_sort(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    response = await client.get(
        "/api/v1/products",
        params={"sort": "invalid_field"},
    )

    assert response.status_code == 422
    service.list_products.assert_not_called()


@pytest.mark.asyncio
async def test_list_products_rejects_invalid_order(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]

    response = await client.get(
        "/api/v1/products",
        params={"order": "sideways"},
    )

    assert response.status_code == 422
    service.list_products.assert_not_called()


@pytest.mark.asyncio
async def test_update_product_rejects_primary_image_removal(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]
    product_id = uuid4()
    image_id = uuid4()

    response = await client.patch(
        f"/api/v1/products/{product_id}",
        data={
            "primary_image_id": str(image_id),
            "removed_image_ids": f'["{image_id}"]',
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == ("Primary image cannot also be removed")
    service.update_product.assert_not_called()


@pytest.mark.asyncio
async def test_delete_product_not_found(
    client,
    product_router_dependencies,
):
    service = product_router_dependencies["service"]
    product_id = uuid4()

    service.delete_product = AsyncMock(
        side_effect=NotFoundException(message="Product not found")
    )

    response = await client.delete(f"/api/v1/products/{product_id}")

    assert response.status_code == 404
    service.delete_product.assert_awaited_once()
