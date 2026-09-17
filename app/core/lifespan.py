from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import aioboto3
from arq import create_pool
from fastapi import FastAPI

from app.core.arq import ARQ_QUEUE_NAME, ARQ_REDIS_SETTINGS
from app.core.settings import settings
from app.db.redis import create_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:

    async with AsyncExitStack() as stack:
        redis = create_redis()
        stack.push_async_callback(redis.aclose)

        await redis.ping()
        app.state.redis = redis

        arq_pool = await create_pool(
            ARQ_REDIS_SETTINGS, default_queue_name=ARQ_QUEUE_NAME
        )
        stack.push_async_callback(arq_pool.aclose)

        app.state.arq_pool = arq_pool

        session = aioboto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=(
                settings.AWS_SECRET_ACCESS_KEY.get_secret_value()
                if settings.AWS_SECRET_ACCESS_KEY is not None
                else None
            ),
        )
        s3_client = await stack.enter_async_context(
            session.client("s3", region_name=settings.AWS_REGION)
        )
        app.state.s3 = s3_client

        yield
