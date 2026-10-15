import secrets

from redis.asyncio import Redis

from app.core.security import hash_passcode
from app.core.settings import settings
from app.exceptions.custom import TooManyRequestsException


def generate_passcode() -> str:
    length = settings.PASSCODE_LENGTH
    return f"{secrets.randbelow(10**length):0{length}d}"


def get_passcode_key(email: str) -> str:
    return f"auth:passcode:{email}"


def get_passcode_attempt_key(email: str) -> str:
    return f"auth:passcode:attempts:{email}"


def get_passcode_request_email_key(email: str) -> str:
    return f"auth:passcode:requests:email:{email}"


def get_passcode_request_ip_key(client_ip: str) -> str:
    return f"auth:passcode:requests:ip:{client_ip}"


async def store_passcode(redis: Redis, email: str, passcode: str) -> None:

    passcode_hash = hash_passcode(passcode)

    key = get_passcode_key(email)

    await redis.set(key, passcode_hash, ex=settings.PASSCODE_EXPIRE_SECONDS)


async def get_passcode(redis: Redis, email: str) -> str | None:

    key = get_passcode_key(email)
    return await redis.get(key)


async def delete_passcode(redis: Redis, email: str) -> None:

    key = get_passcode_key(email)
    await redis.delete(key)


async def consume_passcode(redis: Redis, email: str, expected_hash: str) -> bool:

    result = await redis.eval(
        """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """,
        1,
        get_passcode_key(email),
        expected_hash,
    )
    return result == 1


async def get_passcode_attempts(redis: Redis, email: str) -> int:
    key = get_passcode_attempt_key(email)
    attempts = await redis.get(key)

    return int(attempts or 0)


async def get_passcode_attempt_ttl(redis: Redis, email: str) -> int:

    key = get_passcode_attempt_key(email)
    ttl = await redis.ttl(key)

    return max(ttl, 0)


async def increment_passcode_attempts(redis: Redis, email: str) -> int:
    return await _increment_with_window(
        redis,
        get_passcode_attempt_key(email),
        settings.PASSCODE_EXPIRE_SECONDS,
    )


async def _increment_with_window(
    redis: Redis,
    key: str,
    window_seconds: int,
) -> int:
    added = await redis.set(
        key,
        1,
        ex=window_seconds,
        nx=True,
    )

    if added:
        return 1

    return await redis.incr(key)


async def reset_passcode_attempts(redis: Redis, email: str) -> None:
    key = get_passcode_attempt_key(email)
    await redis.delete(key)


async def check_passcode_request_limit(
    redis: Redis,
    email: str,
    client_ip: str,
) -> None:

    email = email.strip().lower()
    window = settings.PASSCODE_REQUEST_WINDOW_SECONDS

    email_count = await _increment_with_window(
        redis, get_passcode_request_email_key(email), window
    )

    if email_count > settings.PASSCODE_REQUEST_EMAIL_LIMIT:
        raise TooManyRequestsException(
            message="Too many passcode requests. Please try again later.",
        )

    ip_count = await _increment_with_window(
        redis, get_passcode_request_ip_key(client_ip), window
    )

    if ip_count > settings.PASSCODE_REQUEST_IP_LIMIT:
        raise TooManyRequestsException(
            message="Too many passcode requests. Please try again later.",
        )
