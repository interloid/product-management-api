from http.cookies import SimpleCookie
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_auth_service
from app.api.v1.endpoints.oauth_router import router
from app.core.settings import settings
from app.exceptions.custom import UnauthorizedException
from app.exceptions.handlers import register_exception_handlers
from app.schemas.response import ApiResponse
from app.services.auth_service import AuthService


@pytest.fixture
def oauth_app():
    service = AsyncMock(spec=AuthService)
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_auth_service] = lambda: service
    return app, service


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "microsoft", "github"])
async def test_callback_accepts_empty_data_and_sets_token_cookies(
    oauth_app, provider, monkeypatch
):
    app, service = oauth_app
    monkeypatch.setattr(settings, "YOUR_REACT_URL", "https://frontend.example.com/")
    service.oauth_callback.return_value = (
        ApiResponse[None](message="Authentication successful", data=None),
        "test-access-token",
        "test-refresh-token",
        3600,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        response = await client.get(
            f"/auth/{provider}/callback",
            params={"code": "test-code", "state": "test-state"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == settings.YOUR_REACT_URL
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    for name, value, max_age in (
        (
            settings.ACCESS_TOKEN_COOKIE_NAME,
            "test-access-token",
            settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        ),
        (settings.REFRESH_TOKEN_COOKIE_NAME, "test-refresh-token", 3600),
    ):
        cookie = cookies[name]
        assert cookie.value == value
        assert cookie["httponly"]
        assert cookie["secure"]
        assert cookie["samesite"] == "none"
        assert cookie["path"] == "/"
        assert cookie["max-age"] == str(max_age)
    service.oauth_callback.assert_awaited_once_with(
        provider=provider, code="test-code", state="test-state"
    )


@pytest.mark.asyncio
async def test_callback_failure_does_not_set_cookies_or_redirect(oauth_app):
    app, service = oauth_app
    service.oauth_callback.side_effect = UnauthorizedException(
        message="Invalid or expired OAuth state"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        response = await client.get(
            "/auth/google/callback",
            params={"code": "test-code", "state": "invalid-state"},
            follow_redirects=False,
        )

    assert response.status_code == 401
    assert response.json()["message"] == "Invalid or expired OAuth state"
    assert "set-cookie" not in response.headers
    assert "location" not in response.headers
