from arq.connections import ArqRedis
from fastapi import Cookie, Depends, Request
from fastapi.security import HTTPBearer
from jwt import ExpiredSignatureError, InvalidTokenError
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.s3 import S3Service, get_s3_service
from app.core.security import decode_token, validate_access_token_payload
from app.core.settings import settings
from app.db.redis import get_redis
from app.db.session import get_db
from app.exceptions.custom import UnauthorizedException
from app.models.user_model import User
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.cache_service import CacheService
from app.services.product_service import ProductService

bearer_schema = HTTPBearer(
    scheme_name="BearerAuth",
    description="Enter the JWT access token",
    auto_error=False,
)


async def get_cache_service(
    redis: Redis = Depends(get_redis),
) -> CacheService:
    return CacheService(redis)


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
    cache: CacheService = Depends(get_cache_service),
    redis: Redis = Depends(get_redis),
) -> ProductService:
    return ProductService(
        db=db, s3_service=s3_service, arq_pool=arq_pool, cache=cache, redis=redis
    )


async def get_current_user(
    request: Request,
    access_token: str | None = Cookie(
        default=None,
        alias=settings.ACCESS_TOKEN_COOKIE_NAME,
    ),
    refresh_token: str | None = Cookie(
        default=None,
        alias=settings.REFRESH_TOKEN_COOKIE_NAME,
    ),
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
) -> User:

    if access_token is not None:
        try:
            token_payload = validate_access_token_payload(decode_token(access_token))

        except ExpiredSignatureError as exc:
            if refresh_token is None:
                raise UnauthorizedException(message="Invalid access token") from exc

        except (ValidationError, ValueError, InvalidTokenError) as exc:
            raise UnauthorizedException(message="Invalid access token") from exc

        else:
            user = await UserRepository(db).get_by_id(token_payload.sub)
            if user is None or not user.is_active:
                raise UnauthorizedException(message="Invalid access token")
            request.state.rate_limit_user_id = str(user.id)
            return user

    if refresh_token is None:
        raise UnauthorizedException(message="Authentication required")

    (
        user,
        renewed_access_token,
        renewed_refresh_token,
        refresh_max_age,
    ) = await service.renew_access_token(refresh_token)

    request.state.renewed_access_token = renewed_access_token
    request.state.renewed_refresh_token = renewed_refresh_token
    request.state.refresh_max_age = refresh_max_age
    request.state.rate_limit_user_id = str(user.id)
    return user
