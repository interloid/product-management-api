import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints.event_router import events


@pytest.mark.asyncio
async def test_events_rejects_unauthenticated_request(client):
    response = await client.get("/api/v1/events")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_events_subscribes_to_queue():
    queue = asyncio.Queue()
    manager = MagicMock()
    manager.subscribe = AsyncMock(return_value=queue)
    manager.unsubscribe = MagicMock()

    with patch(
        "app.api.v1.endpoints.event_router.sse_manager",
        manager,
    ):
        generator = events()

        task = asyncio.create_task(generator.__anext__())

        try:
            await asyncio.sleep(0)
            manager.subscribe.assert_awaited_once()

            # Cancel the pending queue read.
            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await generator.aclose()

    manager.unsubscribe.assert_called_once_with(queue)
