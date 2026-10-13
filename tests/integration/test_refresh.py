import asyncio
from datetime import timedelta
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI, Response
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_auth_service, get_current_user
from app.api.v1.endpoints.auth_router import router
from app.core.constants import RoleEnum
from app.core.security import create_access_token, decode_token, hash_refresh_token
from app.core.settings import settings
from app.db.session import get_db
from app.exceptions.custom import ForbiddenException
from app.exceptions.handlers import register_exception_handlers
from app.middleware.auth_cookie_middleware import AuthCookieMiddleware
from app.models.refresh_token_model import RefreshToken
from app.models.user_model import User
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.utils.helpers import utc_now


@pytest.fixture
def auth_app(monkeypatch):
    user = User(
        id=uuid4(),
        email="renew@example.com",
        first_name="Renew",
        last_name="User",
        is_active=True,
        role=RoleEnum.VIEWER,
    )
    stored = RefreshToken(
        user_id=user.id,
        family_id=uuid4(),
        token_hash=hash_refresh_token("refresh"),
        expires_at=utc_now() + timedelta(days=1),
        is_revoked=False,
    )
    db = AsyncMock()

    service = AuthService(db=db, redis=AsyncMock(), arq_pool=AsyncMock())

    service.refresh_token_repo = AsyncMock()
    service.refresh_token_repo.get_by_token_hash.return_value = stored

    service.user_repo = AsyncMock()
    service.user_repo.get_by_id.return_value = user

    monkeypatch.setattr(UserRepository, "get_by_id", AsyncMock(return_value=user))

    app = FastAPI()
    app.state.redis = AsyncMock()

    app.add_middleware(AuthCookieMiddleware)
    register_exception_handlers(app)

    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_auth_service] = lambda: service
    app.state.executions = 0

    @app.post("/protected", dependencies=[Depends(get_current_user)])
    async def protected():
        app.state.executions += 1
        return Response(status_code=204)

    @app.get("/forbidden", dependencies=[Depends(get_current_user)])
    async def forbidden():
        raise ForbiddenException(message="permission denied")

    @app.get("/public")
    async def public():
        return {"ok": True}

    return app, service, user, stored


def expired_access(user):
    return jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "exp": utc_now() - timedelta(minutes=1),
        },
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.ALGORITHM,
    )


def cookie_header(access=None, refresh="refresh"):
    cookies = []
    if access is not None:
        cookies.append(f"{settings.ACCESS_TOKEN_COOKIE_NAME}={access}")
    if refresh is not None:
        cookies.append(f"{settings.REFRESH_TOKEN_COOKIE_NAME}={refresh}")
    return {"Cookie": "; ".join(cookies)}


@pytest.mark.parametrize("expired", [False, True])
async def test_request_renews_missing_or_expired_access(auth_app, expired):
    app, service, user, stored = auth_app
    expires_at = stored.expires_at
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.get(
            "/auth/me", headers=cookie_header(expired_access(user) if expired else None)
        )
        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(user.id)

        access = response.cookies[settings.ACCESS_TOKEN_COOKIE_NAME]
        assert decode_token(access)["sub"] == str(user.id)

        cookies = SimpleCookie()
        cookies.load(response.headers["set-cookie"])

        cookie = cookies[settings.ACCESS_TOKEN_COOKIE_NAME]
        assert cookie["secure"] and cookie["httponly"]

        assert cookie["samesite"] == "lax" and cookie["path"] == "/"
        assert cookie["max-age"] == str(settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)

        assert settings.REFRESH_TOKEN_COOKIE_NAME not in response.cookies
        second = await client.get("/auth/me")
        assert second.status_code == 200
        assert "set-cookie" not in second.headers
    service.refresh_token_repo.get_by_token_hash.assert_awaited_once_with(
        hash_refresh_token("refresh")
    )
    service.refresh_token_repo.create.assert_not_awaited()
    assert stored.is_revoked is False and stored.expires_at == expires_at


@pytest.mark.parametrize(
    "access_kind", ["valid", "malformed", "bad_signature", "wrong_type"]
)
async def test_does_not_refresh_valid_or_invalid_access(auth_app, access_kind):
    app, service, user, _ = auth_app
    access = create_access_token({"sub": str(user.id)})
    if access_kind == "malformed":
        access = "not-a-token"
    elif access_kind == "bad_signature":
        access = jwt.encode(
            {
                "sub": str(user.id),
                "type": "access",
                "exp": utc_now() - timedelta(minutes=1),
            },
            "a-different-signing-key-with-enough-characters",
            algorithm=settings.ALGORITHM,
        )
    elif access_kind == "wrong_type":
        access = jwt.encode(
            {
                "sub": str(user.id),
                "type": "refresh",
                "exp": utc_now() + timedelta(minutes=1),
            },
            settings.SECRET_KEY.get_secret_value(),
            algorithm=settings.ALGORITHM,
        )
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.get("/auth/me", headers=cookie_header(access))
    assert response.status_code == (200 if access_kind == "valid" else 401)
    assert "set-cookie" not in response.headers
    service.refresh_token_repo.get_by_token_hash.assert_not_awaited()


@pytest.mark.parametrize(
    "reason", ["missing", "unknown", "expired", "revoked", "inactive", "deleted"]
)
async def test_rejects_unusable_refresh_session(auth_app, reason):
    app, service, user, stored = auth_app
    if reason == "unknown":
        service.refresh_token_repo.get_by_token_hash.return_value = None
    elif reason == "expired":
        stored.expires_at = utc_now() - timedelta(seconds=1)
    elif reason == "revoked":
        stored.is_revoked = True
    elif reason == "inactive":
        user.is_active = False
    elif reason == "deleted":
        service.user_repo.get_by_id.return_value = None
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/protected",
            headers=cookie_header(refresh=None if reason == "missing" else "refresh"),
        )
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert app.state.executions == 0
    if reason == "revoked":
        service.refresh_token_repo.revoke_family.assert_awaited_once_with(
            stored.family_id
        )
        service.db.commit.assert_awaited_once()


async def test_concurrent_requests_execute_once_each_without_rotation(auth_app):
    app, service, user, stored = auth_app
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://test"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post("/protected", headers=cookie_header(expired_access(user)))
                for _ in range(3)
            )
        )
    assert all(response.status_code == 204 for response in responses)
    assert all(
        settings.ACCESS_TOKEN_COOKIE_NAME in response.cookies for response in responses
    )
    assert app.state.executions == 3
    assert stored.is_revoked is False
    service.refresh_token_repo.create.assert_not_awaited()
    service.refresh_token_repo.revoke_family.assert_not_awaited()


async def test_permission_denial_still_delivers_renewed_cookie(auth_app):
    app, _, _, _ = auth_app
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.get("/forbidden", headers=cookie_header())
    assert response.status_code == 403
    assert settings.ACCESS_TOKEN_COOKIE_NAME in response.cookies


async def test_logout_all_cookie_deletion_takes_precedence(auth_app):
    app, service, user, _ = auth_app
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.post("/auth/logout-all", headers=cookie_header())
    assert response.status_code == 204
    cookies = SimpleCookie()
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 2
    for header in headers:
        cookies.load(header)
    assert cookies[settings.ACCESS_TOKEN_COOKIE_NAME]["max-age"] == "0"
    assert cookies[settings.REFRESH_TOKEN_COOKIE_NAME]["max-age"] == "0"
    service.refresh_token_repo.revoke_all_for_user.assert_awaited_once_with(user.id)


async def test_public_request_does_not_refresh(auth_app):
    app, service, user, _ = auth_app
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.get(
            "/public", headers=cookie_header(expired_access(user))
        )
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    service.refresh_token_repo.get_by_token_hash.assert_not_awaited()
