from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_auth_service
from app.api.v1.endpoints.oauth_router import router as oauth_router
from app.core.settings import settings
from app.exceptions.handlers import register_exception_handlers
from app.schemas.response import ApiResponse
from app.services.auth_service import AuthService


@pytest.fixture
def oauth_app():
    service = AsyncMock(spec=AuthService)

    app = FastAPI()
    app.include_router(oauth_router)

    register_exception_handlers(app)

    app.dependency_overrides[get_auth_service] = lambda: service

    return app, service


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "microsoft", "github"])
async def test_oauth(oauth_app, provider):

    app, service = oauth_app

    authorization_url = f"https://pms/{provider}/authorize"
    service.start_oauth.return_value = authorization_url

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(f"/auth/{provider}")

        assert response.status_code == 302
        assert response.headers["location"] == authorization_url

        service.start_oauth.assert_awaited_once_with(
            provider=provider,
        )


@pytest.mark.asyncio
async def test_oauth_callback_error(oauth_app):

    app, service = oauth_app

    login_url = f"{settings.YOUR_REACT_URL}/login"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/auth/google/callback", params={"error": "access_denied"}
        )

        assert response.status_code == 302
        assert response.headers["location"] == (f"{login_url}?error=oauth_denied")


@pytest.mark.asyncio
async def test_oauth_callback_no_state(oauth_app):

    app, service = oauth_app

    login_url = f"{settings.YOUR_REACT_URL}/login"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/auth/google/callback", params={"state": "test-state"}
        )

        assert response.status_code == 302
        assert response.headers["location"] == (f"{login_url}?error=oauth_denied")


@pytest.mark.asyncio
async def test_oauth_callback_no_code(oauth_app):

    app, service = oauth_app

    login_url = f"{settings.YOUR_REACT_URL}/login"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/auth/google/callback", params={"code": "test-code"}
        )

        assert response.status_code == 302
        assert response.headers["location"] == (f"{login_url}?error=oauth_denied")


@pytest.mark.asyncio
async def test_callback_accepts(oauth_app):

    app, service = oauth_app

    service.oauth_callback.return_value = (
        ApiResponse[None](message="Authentication successful", data=None),
        "test-access-token",
        "test-refresh-token",
        3600,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/auth/google/callback",
            params={"code": "test-code", "state": "test-state"},
        )

        assert response.status_code == 302
        assert response.headers["location"] == settings.YOUR_REACT_URL
        service.oauth_callback.assert_awaited_once_with(
            provider="google",
            code="test-code",
            state="test-state",
        )

        assert "set-cookie" in response.headers
