from typing import Any

from arq import Retry

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger(__name__)


async def delete_s3_jobs(ctx: dict[str, Any], object_keys: list[str]) -> None:
    try:
        for object_key in object_keys:
            await ctx["s3"].delete_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=object_key,
            )

    except Exception as exc:
        retry_delay = min(ctx.get("job_try", 1) * 10, 60)
        logger.exception("S3 object deletion failed")
        raise Retry(defer=retry_delay) from exc
