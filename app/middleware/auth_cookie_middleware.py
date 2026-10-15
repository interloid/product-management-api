from http.cookies import SimpleCookie

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.cookies import set_cookie
from app.core.settings import settings


class AuthCookieMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        access_token = getattr(request.state, "renewed_access_token", None)
        refresh_token = getattr(request.state, "renewed_refresh_token", None)
        refresh_max_age = getattr(request.state, "refresh_max_age", None)

        if (
            access_token is not None
            and refresh_token is not None
            and refresh_max_age is not None
        ):
            for header in response.headers.getlist("set-cookie"):
                cookies = SimpleCookie()
                cookies.load(header)
                if settings.ACCESS_TOKEN_COOKIE_NAME in cookies:
                    return response
            set_cookie(
                response=response,
                access_token=access_token,
                raw_refresh_token=refresh_token,
                refresh_max_age=refresh_max_age,
            )

        return response
