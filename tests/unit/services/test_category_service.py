import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.custom import BadRequestException
from app.models.category_model import Category
from app.schemas.response import PaginatedResponse
from app.services.category_service import CategoryService


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_cache():
    cache = MagicMock()
    cache.get_json = AsyncMock()
    cache.set_json = AsyncMock()
    return cache


@pytest.fixture
def category_service(mock_db, mock_cache):
    return CategoryService(db=mock_db, cache=mock_cache)


async def test_get_categories_cache_hit(category_service, mock_cache, mock_db):
    cached_payload = {
        "success": True,
        "message": "Categories retrieved successfully",
        "data": [
            {
                "id": str(uuid.uuid4()),
                "name": "Electronics",
                "description": "Electronic gadgets",
                "total_products": 10,
            }
        ],
        "pagination": {
            "page": 1,
            "page_size": 10,
            "total": 1,
            "total_pages": 1,
        },
    }
    mock_cache.get_json.return_value = cached_payload

    result = await category_service.get_categories(search="elect", page=1, page_size=10)

    assert isinstance(result, PaginatedResponse)
    assert len(result.data) == 1
    assert result.data[0].name == "Electronics"
    mock_db.execute.assert_not_called()


async def test_get_categories_cache_miss(category_service, mock_cache, mock_db):
    mock_cache.get_json.return_value = None

    cat_id = uuid.uuid4()
    mock_cat = MagicMock(spec=Category)
    mock_cat.id = cat_id
    mock_cat.name = "Electronics"
    mock_cat.description = "Devices and gadgets"

    count_result = MagicMock()
    count_result.scalar_one.return_value = 1

    cats_result = MagicMock()
    cats_result.all.return_value = [(mock_cat, 3)]

    mock_db.execute.side_effect = [count_result, cats_result]

    result = await category_service.get_categories(search=None, page=1, page_size=10)

    assert isinstance(result, PaginatedResponse)
    assert len(result.data) == 1
    assert result.data[0].id == cat_id
    assert result.data[0].name == "Electronics"
    assert result.data[0].total_products == 3
    assert result.pagination.total == 1
    assert result.pagination.total_pages == 1

    mock_cache.set_json.assert_called_once()
    call_args = mock_cache.set_json.call_args[1]
    assert call_args["ttl"] == 300
    assert call_args["value"]["data"][0]["name"] == "Electronics"


async def test_get_categories_invalid_pagination(category_service):
    with pytest.raises(BadRequestException):
        await category_service.get_categories(page=0, page_size=10)
