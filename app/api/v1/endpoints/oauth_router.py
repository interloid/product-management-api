from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.api.dependencies import get_auth_service
from app.api.v1.endpoints.auth_router import set_cookie
from app.core.settings import settings
from app.exceptions.global_exception import AUTH_ERROR_RESPONSES
from app.services.auth_service import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["OAUTH"],
)


@router.get(
    "/{provider}",
    responses=AUTH_ERROR_RESPONSES,
)
async def oauth(
    provider: str,
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    authorization_url = await service.start_oauth(
        provider=provider,
    )

    return RedirectResponse(
        url=authorization_url,
        status_code=302,
    )


@router.get(
    "/{provider}/callback",
    responses=AUTH_ERROR_RESPONSES,
)
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    (
        _result,
        access_token,
        raw_refresh_token,
        refresh_max_age,
    ) = await service.oauth_callback(
        provider=provider,
        code=code,
        state=state,
    )

    response = RedirectResponse(
        url=settings.YOUR_REACT_URL,
        status_code=302,
    )

    set_cookie(
        response=response,
        access_token=access_token,
        raw_refresh_token=raw_refresh_token,
        refresh_max_age=refresh_max_age,
    )

    return response
