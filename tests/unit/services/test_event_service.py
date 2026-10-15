import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import RedisError

from app.core.constants import EventChannelConstants, EventType
from app.schemas.event import EventEnvelope
from app.services.event_service import EventService
from app.utils.helpers import utc_now


@pytest.mark.asyncio
async def test_event_service_publish():
    redis = AsyncMock()
    service = EventService(redis=redis)

    envelope = EventEnvelope(
        event=EventType.PRODUCT_CREATED,
        request_id="req-123",
        timestamp=utc_now(),
        data={"product_id": "p-1"},
    )

    await service.publish(envelope)

    redis.publish.assert_awaited_once()
    args = redis.publish.call_args.args
    assert args[0] == EventChannelConstants.SSE_EVENTS_CHANNEL
    published_dict = json.loads(args[1])
    assert published_dict["event"] == EventType.PRODUCT_CREATED
    assert published_dict["request_id"] == "req-123"
    assert published_dict["data"] == {"product_id": "p-1"}


@pytest.mark.asyncio
async def test_event_service_subscribe_message_flow():
    redis = MagicMock()
    pubsub = MagicMock()
    redis.pubsub.return_value = pubsub
    pubsub.subscribe = AsyncMock(return_value="channel-ok")
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    envelope = EventEnvelope(
        event=EventType.PRODUCT_UPDATED,
        request_id="req-456",
        timestamp=utc_now(),
        data={"product_id": "p-2"},
    )

    # Sequence of messages:
    # 1. Non-message type (e.g. subscribe confirmation)
    # 2. Real message with envelope JSON
    # 3. CancelledError to break the while loop
    async def fake_listen():
        yield {"type": "subscribe", "data": "dummy"}
        yield {"type": "message", "data": json.dumps(envelope.model_dump(mode="json"))}
        raise asyncio.CancelledError()

    pubsub.listen = fake_listen

    service = EventService(redis=redis)

    with patch(
        "app.services.event_service.sse_manager.broadcast", new_callable=AsyncMock
    ) as mock_broadcast:
        with pytest.raises(asyncio.CancelledError):
            await service.subscribe()

        mock_broadcast.assert_awaited_once()
        sse_event = mock_broadcast.call_args.args[0]
        assert sse_event.event == EventType.PRODUCT_UPDATED
        assert sse_event.id == "req-456"
        assert sse_event.data == {"product_id": "p-2"}

    pubsub.unsubscribe.assert_awaited_once_with(
        EventChannelConstants.SSE_EVENTS_CHANNEL
    )
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_service_subscribe_redis_error_and_generic_error():
    redis = MagicMock()
    pubsub = MagicMock()
    redis.pubsub.return_value = pubsub
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    attempts = 0

    async def fake_subscribe(channel):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RedisError("Connection lost")
        elif attempts == 2:
            raise RuntimeError("Unexpected error")
        else:
            raise asyncio.CancelledError()

    pubsub.subscribe = fake_subscribe

    service = EventService(redis=redis)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(asyncio.CancelledError):
            await service.subscribe()

        assert mock_sleep.await_count >= 2
        # Verify backoff happened
        assert mock_sleep.call_args_list[0].args[0] == 1
        assert mock_sleep.call_args_list[1].args[0] == 2
