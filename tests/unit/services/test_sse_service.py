import asyncio

import pytest

from app.services.sse_service import SSEConnectionManager


@pytest.mark.asyncio
async def test_sse_connection_manager_lifecycle():
    manager = SSEConnectionManager()
    assert len(manager.connections) == 0

    queue = await manager.subscribe()
    assert queue in manager.connections
    assert len(manager.connections) == 1

    event_data = {"msg": "hello"}
    await manager.broadcast(event_data)
    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received == event_data

    manager.unsubscribe(queue)
    assert queue not in manager.connections
    assert len(manager.connections) == 0

    manager.unsubscribe(queue)
