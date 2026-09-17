from contextlib import AsyncExitStack
from datetime import UTC
from typing import Any

import aioboto3
from arq import cron

from app.core.arq import ARQ_QUEUE_NAME, ARQ_REDIS_SETTINGS
from app.core.settings import settings
from app.db.database import engine
from app.db.session import SessionLocal
from app.jobs.email_jobs import send_passcode_email_job
from app.jobs.image_jobs import IMAGE_JOB_MAX_TRIES, upload_product_images
from app.jobs.refresh_token_jobs import cleanup_expired_refresh_tokens_jobs
from app.jobs.s3_jobs import delete_s3_jobs


async def startup(ctx: dict[str, Any]) -> None:
    stack = AsyncExitStack()
    await stack.__aenter__()

    session_options = {}

    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        session_options = {
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": (
                settings.AWS_SECRET_ACCESS_KEY.get_secret_value()
            ),
        }

    session = aioboto3.Session(**session_options)

    ctx["s3"] = await stack.enter_async_context(
        session.client("s3", region_name=settings.AWS_REGION)
    )
    ctx["db_session_factory"] = SessionLocal
    ctx["db_engine"] = engine

    ctx["exit_stack"] = stack


async def shutdown(ctx: dict[str, Any]) -> None:
    stack = ctx.get("exit_stack")
    db_engine = ctx.get("db_engine")

    if stack is not None:
        await stack.aclose()

    if db_engine is not None:
        await db_engine.dispose()


class WorkerSettings:
    functions = [
        send_passcode_email_job,
        delete_s3_jobs,
        upload_product_images,
    ]

    cron_jobs = [
        cron(
            cleanup_expired_refresh_tokens_jobs,
            hour=2,
            minute=0,
            run_at_startup=False,
            unique=True,
            max_tries=5,
        )
    ]

    redis_settings = ARQ_REDIS_SETTINGS
    queue_name = ARQ_QUEUE_NAME

    max_tries = IMAGE_JOB_MAX_TRIES
    job_timeout = 300
    keep_result = 86_400
    log_results = False

    on_startup = startup
    on_shutdown = shutdown

    timezone = UTC
