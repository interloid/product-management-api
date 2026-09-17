from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis

from app.api.dependencies import get_auth_service
from app.api.v1.endpoints.auth_router import set_cookie
from app.db.redis import get_redis
from app.exceptions.global_exception import AUTH_ERROR_RESPONSES
from app.schemas.auth_schema import (
    LoginResponse,
    PasscodeRequest,
    PasscodeVerifyRequest,
)
from app.schemas.response import ApiResponse
from app.services.auth_service import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["Email Authentication"],
)


@router.post(
    "/passcode/requests",
    response_model=ApiResponse[None],
    responses=AUTH_ERROR_RESPONSES,
)
async def request_passcode(
    request: Request,
    login_data: PasscodeRequest,
    redis: Redis = Depends(get_redis),
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[None]:
    await service.request_passcode(
        email=login_data.email,
        client_ip=request.client.host if request.client else "unknown",
        redis=redis,
    )

    return ApiResponse[None](
        message="Verification code sent to the email is registered.",
    )


@router.post(
    "/passcode/verifications",
    response_model=ApiResponse[LoginResponse],
    responses=AUTH_ERROR_RESPONSES,
)
async def verify_passcode(
    login_data: PasscodeVerifyRequest,
    response: Response,
    redis: Redis = Depends(get_redis),
    service: AuthService = Depends(get_auth_service),
) -> tuple[ApiResponse[None], str, str, int]:

    (
        result,
        access_token,
        raw_refresh_token,
        refresh_max_age,
    ) = await service.verify_email_passcode(
        email=login_data.email,
        passcode=login_data.passcode,
        redis=redis,
    )

    set_cookie(
        response=response,
        access_token=access_token,
        raw_refresh_token=raw_refresh_token,
        refresh_max_age=refresh_max_age,
    )

    return result
