from datetime import UTC, datetime
from typing import Any

from arq import Retry

from app.core.logging import get_logger
from app.repositories.refresh_token_repo import RefreshTokenRepository

logger = get_logger(__name__)


async def cleanup_expired_refresh_tokens_jobs(ctx: dict[str, Any]) -> None:

    try:
        session_factory = ctx["db_session_factory"]

        async with session_factory() as db:
            async with db.begin():
                repository = RefreshTokenRepository(db)
                deleted_count = await repository.delete_expired(now=datetime.now(UTC))

        logger.info(
            "Expired refresh tokens deleted | count=%s",
            deleted_count,
        )

    except Exception as exc:
        retry_delay = min(ctx.get("job_try", 1) * 30, 300)
        logger.exception("Refresh-token cleanup failed")
        raise Retry(defer=retry_delay) from exc
