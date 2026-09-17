from typing import Any
from urllib.parse import urlsplit

from arq.connections import RedisSettings
from redis.asyncio.connection import parse_url as parse_redis_url

from app.core.settings import settings


def build_arq_redis_settings() -> RedisSettings:

    redis_url = settings.REDIS_URL
    if redis_url:
        redis_url_string = str(redis_url)

        parsed_url = urlsplit(redis_url_string)

        if parsed_url.scheme not in {"redis", "rediss"}:
            raise ValueError("Redis url must use redis:// or rediss://")

        if parsed_url.hostname is None:
            raise ValueError("REDIS_URL must contain a hostname")

        options: dict[str, Any] = parse_redis_url(
            redis_url_string,
        )

        database = int(options.get("db", 0))

        username = options.get("username")
        password = options.get("password")

        return RedisSettings(
            host=parsed_url.hostname,
            port=parsed_url.port or 6379,
            database=database,
            username=(str(username) if username is not None else None),
            password=(str(password) if password is not None else None),
            ssl=parsed_url.scheme == "rediss",
        )

    return RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DB,
        username=settings.REDIS_USERNAME or None,
        password=settings.REDIS_PASSWORD or None,
        ssl=False,
    )


ARQ_REDIS_SETTINGS = build_arq_redis_settings()
ARQ_QUEUE_NAME = "pms:arq:queue"
