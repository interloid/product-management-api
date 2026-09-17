from fastapi import APIRouter, Cookie, Depends, Response, status

from app.api.dependencies import get_auth_service, get_current_user
from app.core.settings import settings
from app.exceptions.global_exception import AUTH_ERROR_RESPONSES
from app.models.user_model import User
from app.schemas.auth_schema import (
    LoginRequest,
)
from app.schemas.response import ApiResponse
from app.schemas.user_schema import UserResponse
from app.services.auth_service import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["JWT Authentication"],
)


def set_cookie(
    *,
    response: Response,
    access_token: str,
    raw_refresh_token: str,
    refresh_max_age: int,
) -> None:

    response.set_cookie(
        key=settings.ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    response.set_cookie(
        key=settings.REFRESH_TOKEN_COOKIE_NAME,
        value=raw_refresh_token,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=refresh_max_age,
    )


def delete_cookie(response: Response) -> None:

    response.delete_cookie(
        key=settings.ACCESS_TOKEN_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="none",
    )

    response.delete_cookie(
        key=settings.REFRESH_TOKEN_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="none",
    )


@router.post(
    "/login",
    response_model=ApiResponse[None],
    responses=AUTH_ERROR_RESPONSES,
)
async def login(
    login_data: LoginRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> tuple[ApiResponse[None], str, str, int]:

    (result, access_token, raw_refresh_token, refresh_max_age) = await service.login(
        login_data
    )

    set_cookie(
        response=response,
        access_token=access_token,
        raw_refresh_token=raw_refresh_token,
        refresh_max_age=refresh_max_age,
    )

    return result


@router.post(
    "/refresh",
    response_model=ApiResponse[None],
    responses=AUTH_ERROR_RESPONSES,
)
async def refresh_token(
    response: Response,
    refresh_token: str | None = Cookie(
        default=None,
        alias=settings.REFRESH_TOKEN_COOKIE_NAME,
    ),
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[None]:
    (
        result,
        access_token,
        new_raw_refresh_token,
        refresh_max_age,
    ) = await service.refresh_token(
        raw_refresh_token=refresh_token,
    )

    set_cookie(
        response=response,
        access_token=access_token,
        raw_refresh_token=new_raw_refresh_token,
        refresh_max_age=refresh_max_age,
    )

    return result


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=AUTH_ERROR_RESPONSES,
)
async def logout_current_device(
    response: Response,
    refresh_token: str | None = Cookie(
        default=None,
        alias=settings.REFRESH_TOKEN_COOKIE_NAME,
    ),
    service: AuthService = Depends(get_auth_service),
) -> None:
    await service.logout_current_device(refresh_token)

    delete_cookie(response=response)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=AUTH_ERROR_RESPONSES,
)
async def logout_all_devices(
    response: Response,
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
) -> None:
    await service.logout_all_devices(user_id=current_user.id)

    delete_cookie(response=response)


@router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
    responses=AUTH_ERROR_RESPONSES,
)
async def get_current_user_details(
    current_user: User = Depends(get_current_user),
):
    return ApiResponse[UserResponse](
        message="Current user retrieved successfully",
        data=UserResponse(
            id=current_user.id,
            email=current_user.email,
            first_name=current_user.first_name,
            last_name=current_user.last_name,
            avatar_url=current_user.avatar_url,
            is_active=current_user.is_active,
            role=current_user.role,
        ),
    )
