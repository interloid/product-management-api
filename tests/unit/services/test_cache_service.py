import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.core.constants import CacheKeyConstants
from app.services.cache_service import CacheService


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.get = AsyncMock()
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    redis.sadd = AsyncMock()
    redis.smembers = AsyncMock()
    return redis


@pytest.fixture
def cache_service(mock_redis):
    return CacheService(redis=mock_redis)


@pytest.mark.asyncio
async def test_get_json_success(cache_service, mock_redis):
    data = {"hello": "world", "num": 123}
    mock_redis.get.return_value = json.dumps(data)

    result = await cache_service.get_json("test-key")
    assert result == data
    mock_redis.get.assert_awaited_once_with("test-key")


@pytest.mark.asyncio
async def test_get_json_redis_error(cache_service, mock_redis):
    mock_redis.get.side_effect = RedisError("Connection error")

    result = await cache_service.get_json("test-key")
    assert result is None


@pytest.mark.asyncio
async def test_get_json_none_value(cache_service, mock_redis):
    mock_redis.get.return_value = None

    result = await cache_service.get_json("test-key")
    assert result is None


@pytest.mark.asyncio
async def test_get_json_invalid_json(cache_service, mock_redis):
    mock_redis.get.return_value = "invalid-json{"

    result = await cache_service.get_json("test-key")
    assert result is None


@pytest.mark.asyncio
async def test_set_json_success(cache_service, mock_redis):
    data = {"key": "val"}
    await cache_service.set_json("my-key", data, ttl=60)

    mock_redis.set.assert_awaited_once()
    args, kwargs = mock_redis.set.call_args
    assert args[0] == "my-key"
    assert json.loads(args[1]) == data
    assert kwargs.get("ex") == 60


@pytest.mark.asyncio
async def test_set_json_redis_error(cache_service, mock_redis):
    mock_redis.set.side_effect = RedisError("Write failed")
    result = await cache_service.set_json("my-key", {"a": 1}, ttl=60)
    assert result is None


@pytest.mark.asyncio
async def test_delete_keys_empty(cache_service, mock_redis):
    await cache_service.delete_keys()
    mock_redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_keys_success(cache_service, mock_redis):
    await cache_service.delete_keys("k1", "k2")
    mock_redis.delete.assert_awaited_once_with("k1", "k2")


@pytest.mark.asyncio
async def test_delete_keys_redis_error(cache_service, mock_redis):
    mock_redis.delete.side_effect = RedisError("Delete failed")
    result = await cache_service.delete_keys("k1")
    assert result is None


@pytest.mark.asyncio
async def test_delete_by_pattern_with_keys(cache_service, mock_redis):
    async def fake_scan_iter(match):
        yield "found-k1"
        yield "found-k2"

    mock_redis.scan_iter = fake_scan_iter
    await cache_service.delete_by_pattern("pattern:*")
    mock_redis.delete.assert_awaited_once_with("found-k1", "found-k2")


@pytest.mark.asyncio
async def test_delete_by_pattern_no_keys(cache_service, mock_redis):
    async def fake_scan_iter(match):
        if False:
            yield None

    mock_redis.scan_iter = fake_scan_iter
    await cache_service.delete_by_pattern("empty:*")
    mock_redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_by_pattern_redis_error(cache_service, mock_redis):
    def fake_scan_iter_error(match):
        raise RedisError("Scan error")

    mock_redis.scan_iter = fake_scan_iter_error
    await cache_service.delete_by_pattern("pattern:*")
    mock_redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_product_list_cache_empty(cache_service, mock_redis):
    mock_redis.smembers.return_value = set()
    await cache_service.invalidate_product_list_cache()
    mock_redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_product_list_cache_with_keys(cache_service, mock_redis):
    mock_redis.smembers.return_value = {b"key1", "key2"}
    await cache_service.invalidate_product_list_cache()
    assert mock_redis.delete.await_count == 2
    # First delete deletes member keys, second deletes the set key itself
    all_delete_calls = mock_redis.delete.call_args_list
    first_call_args = set(all_delete_calls[0].args)
    assert first_call_args == {"key1", "key2"}
    assert all_delete_calls[1].args[0] == CacheKeyConstants.PRODUCT_LIST_CACHE_KEYS


@pytest.mark.asyncio
async def test_invalidate_product_list_cache_redis_error(cache_service, mock_redis):
    mock_redis.smembers.side_effect = RedisError("smembers failed")
    await cache_service.invalidate_product_list_cache()
    mock_redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_product_cache(cache_service, mock_redis):
    prod_id = uuid4()
    await cache_service.invalidate_product_cache(prod_id)
    mock_redis.delete.assert_awaited_once_with(
        f"{CacheKeyConstants.PRODUCT_CACHE_PREFIX}:{prod_id}"
    )


@pytest.mark.asyncio
async def test_invalidate_category_list_cache(cache_service, mock_redis):
    async def fake_scan_iter(match):
        yield "categories:list:hash1"

    mock_redis.scan_iter = fake_scan_iter
    await cache_service.invalidate_category_list_cache()
    mock_redis.delete.assert_awaited_once_with("categories:list:hash1")


@pytest.mark.asyncio
async def test_add_to_set_success(cache_service, mock_redis):
    await cache_service.add_to_set("set-key", "value-1")
    mock_redis.sadd.assert_awaited_once_with("set-key", "value-1")


@pytest.mark.asyncio
async def test_add_to_set_redis_error(cache_service, mock_redis):
    mock_redis.sadd.side_effect = RedisError("sadd failed")
    await cache_service.add_to_set("set-key", "value-1")
    mock_redis.sadd.assert_awaited_once_with("set-key", "value-1")
