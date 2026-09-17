from arq.connections import ArqRedis
from fastapi import Cookie, Depends, Request
from fastapi.security import HTTPBearer
from jwt import ExpiredSignatureError, InvalidTokenError
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.s3 import S3Service, get_s3_service
from app.core.security import decode_token, validate_access_token_payload
from app.db.redis import get_redis
from app.db.session import get_db
from app.exceptions.custom import UnauthorizedException
from app.models.user_model import User
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.product_service import ProductService

bearer_schema = HTTPBearer(
    scheme_name="BearerAuth",
    description="Enter the JWT access token",
    auto_error=False,
)


def get_arq_pool(request: Request) -> ArqRedis:
    return request.app.state.arq_pool


def get_auth_service(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    arq_pool: ArqRedis = Depends(get_arq_pool),
) -> AuthService:
    return AuthService(db=db, redis=redis, arq_pool=arq_pool)


def get_product_service(
    db: AsyncSession = Depends(get_db),
    s3_service: S3Service = Depends(get_s3_service),
    arq_pool: ArqRedis = Depends(get_arq_pool),
) -> ProductService:
    return ProductService(
        db=db,
        s3_service=s3_service,
        arq_pool=arq_pool,
    )


async def get_current_user(
    access_token: str | None = Cookie(default=None, alias="access_token"),
    db: AsyncSession = Depends(get_db),
) -> User:

    if access_token is None:
        raise UnauthorizedException(message="Authentication required")

    try:
        decode_payload = decode_token(access_token)

        token_payload = validate_access_token_payload(decode_payload)

    except ExpiredSignatureError as exc:
        raise UnauthorizedException(message="Invalid access token") from exc

    except (ValidationError, ValueError, InvalidTokenError) as exc:
        raise UnauthorizedException(message="Invalid access token") from exc

    user = await UserRepository(db).get_by_id(token_payload.sub)

    if user is None or not user.is_active:
        raise UnauthorizedException(message="Invalid access token")

    return user
