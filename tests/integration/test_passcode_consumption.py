import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.passcode import (
    consume_passcode,
    get_passcode,
    get_passcode_attempt_key,
    get_passcode_key,
    store_passcode,
)
from app.core.security import hash_passcode
from app.db.redis import create_redis
from app.exceptions.custom import UnauthorizedException
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def passcode_store():
    redis = create_redis()
    email = f"consume-{uuid4()}@example.com"
    try:
        yield redis, email
    finally:
        await redis.delete(get_passcode_key(email), get_passcode_attempt_key(email))
        await redis.aclose()


def make_service(redis, user):
    service = AuthService(db=AsyncMock(), redis=redis, arq_pool=MagicMock())
    service.user_repo = MagicMock()
    service.user_repo.get_by_email = AsyncMock(return_value=user)
    service.user_repo.create = AsyncMock()
    service._issue_token_pair = AsyncMock(return_value=("access", "refresh", 3600))
    return service


async def test_concurrent_consumption_has_exactly_one_winner(passcode_store):
    redis, email = passcode_store
    await store_passcode(redis, email, "123456")

    results = await asyncio.gather(
        *(consume_passcode(redis, email, hash_passcode("123456")) for _ in range(20))
    )

    assert results.count(True) == 1
    assert await get_passcode(redis, email) is None


@pytest.mark.parametrize("existing_user", [False, True])
async def test_concurrent_verification_issues_tokens_once(
    passcode_store, monkeypatch, existing_user
):
    redis, email = passcode_store
    await store_passcode(redis, email, "123456")
    user = MagicMock(is_active=True) if existing_user else None
    services = [make_service(redis, user) for _ in range(2)]
    both_read = asyncio.Event()
    reads = 0

    async def synchronized_get_passcode(*, redis, email):
        nonlocal reads
        stored_hash = await get_passcode(redis, email)
        reads += 1
        if reads == 2:
            both_read.set()
        await asyncio.wait_for(both_read.wait(), timeout=5)
        return stored_hash

    monkeypatch.setattr(
        "app.services.auth_service.get_passcode", synchronized_get_passcode
    )
    results = await asyncio.gather(
        *(
            service.verify_email_passcode(redis=redis, email=email, passcode="123456")
            for service in services
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, UnauthorizedException) for result in results) == 1
    assert sum(service._issue_token_pair.await_count for service in services) == 1
    assert sum(service.user_repo.create.await_count for service in services) == (
        0 if existing_user else 1
    )
    assert await get_passcode(redis, email) is None


@pytest.mark.parametrize("change", ["replacement", "expiration"])
async def test_changed_passcode_cannot_authorize_login(
    passcode_store, monkeypatch, change
):
    redis, email = passcode_store
    await store_passcode(redis, email, "123456")
    service = make_service(redis, None)

    async def get_then_change_passcode(*, redis, email):
        stored_hash = await get_passcode(redis, email)
        if change == "replacement":
            await store_passcode(redis, email, "654321")
        else:
            await redis.pexpire(get_passcode_key(email), 0)
        return stored_hash

    monkeypatch.setattr(
        "app.services.auth_service.get_passcode", get_then_change_passcode
    )
    with pytest.raises(UnauthorizedException):
        await service.verify_email_passcode(redis=redis, email=email, passcode="123456")

    service.user_repo.create.assert_not_awaited()
    service._issue_token_pair.assert_not_awaited()
    expected = hash_passcode("654321") if change == "replacement" else None
    assert await get_passcode(redis, email) == expected


async def test_incorrect_passcode_preserves_code_and_counts_attempt(passcode_store):
    redis, email = passcode_store
    await store_passcode(redis, email, "123456")
    service = make_service(redis, None)

    with pytest.raises(UnauthorizedException):
        await service.verify_email_passcode(redis=redis, email=email, passcode="654321")

    assert await get_passcode(redis, email) == hash_passcode("123456")
    assert int(await redis.get(get_passcode_attempt_key(email))) == 1
    service.user_repo.create.assert_not_awaited()
    service._issue_token_pair.assert_not_awaited()
