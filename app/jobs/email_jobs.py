import json
from typing import Any, NoReturn

from arq import Retry
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.services.email_services import (
    send_passcode_email,
)

logger = get_logger(__name__)

EMAIL_JOB_MAX_TRIES = 5


def _retry_or_raise(
    ctx: dict[str, Any],
    email_job_id: str,
    operation: str,
    exc: Exception,
) -> NoReturn:
    job_try = int(ctx.get("job_try", 1))

    if job_try > EMAIL_JOB_MAX_TRIES:
        logger.error(
            "Passcode email job permanently failed "
            "| email_job_id=%s| operation=%s "
            "| attempts=%s | error=%s",
            email_job_id,
            operation,
            job_try,
            exc,
        )

        raise exc

    retry_delay = min(job_try * 10, 60)

    logger.warning(
        "Passcode email job failed; retrying "
        "| email_job_id=%s | operation=%s "
        "| attempt=%s | retry_delay=%s "
        "| error=%s",
        email_job_id,
        operation,
        job_try,
        retry_delay,
        exc,
    )

    raise Retry(defer=retry_delay) from exc


async def send_passcode_email_job(
    ctx: dict[str, Any],
    email_job_id: str,
) -> dict[str, str]:

    redis = ctx["redis"]

    key = f"jobs:passcode-email:{email_job_id}"
    sent_key = f"jobs:passcode-email:sent:{email_job_id}"

    try:
        already_sent = await redis.exists(
            sent_key,
        )

        if already_sent:
            logger.info(
                "Passcode email already sent | email_job_id=%s",
                email_job_id,
            )

            return {
                "status": "skipped",
                "reason": "already_sent",
            }

        raw_data = await redis.get(key)

    except RedisError as exc:
        _retry_or_raise(
            ctx=ctx,
            email_job_id=email_job_id,
            operation="load_payload",
            exc=exc,
        )

    if raw_data is None:
        logger.info(
            "Passcode email payload not found | email_job_id=%s",
            email_job_id,
        )

        return {
            "status": "skipped",
            "reason": "payload_not_found",
        }

    try:
        email_data = json.loads(raw_data)

        to_email = email_data["to_email"]
        first_name = email_data["first_name"]
        passcode = email_data["passcode"]
        expiry_minutes = int(email_data["expiry_minutes"])

    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        logger.exception(
            "Invalid passcode email payload | email_job_id=%s",
            email_job_id,
        )

        raise ValueError(
            "Invalid passcode email payload",
        ) from exc

    try:
        await send_passcode_email(
            to_email=to_email,
            first_name=first_name,
            passcode=passcode,
            expiry_minutes=expiry_minutes,
        )

    except Exception as exc:
        _retry_or_raise(
            ctx=ctx,
            email_job_id=email_job_id,
            operation="send_email",
            exc=exc,
        )

    try:
        pipeline = redis.pipeline(
            transaction=True,
        )

        pipeline.set(
            sent_key,
            "1",
            ex=86_400,
        )
        pipeline.delete(key)

        await pipeline.execute()

    except RedisError as exc:
        _retry_or_raise(
            ctx=ctx,
            email_job_id=email_job_id,
            operation="save_sent_marker",
            exc=exc,
        )

    logger.info(
        "Passcode email sent | email_job_id=%s",
        email_job_id,
    )

    return {
        "status": "sent",
        "email_job_id": email_job_id,
    }
