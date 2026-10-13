from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from app.core.settings import settings
from app.exceptions.custom import (
    NotFoundException,
    ServiceUnavailableException,
    TooManyRequestsException,
    UnauthorizedException,
)
from app.services.auth_service import (
    OAUTH_PROVIDERS,
    AuthService,
)
from app.utils.helpers import utc_now


@pytest.fixture
async def test_auth_service():

    db = AsyncMock()
    redis = AsyncMock()
    arq_pool = AsyncMock()

    service = AuthService(db=db, redis=redis, arq_pool=arq_pool)

    service.user_repo = MagicMock()
    service.refresh_token_repo = MagicMock()
    service.user_identity_repo = MagicMock()
    service.oauth_state_repo = MagicMock()

    return service


def make_user(
    *,
    email="user@example.com",
    password="hashed-password",
    first_name="John",
    last_name="Doe",
    is_active=True,
):
    user = MagicMock()

    user.id = uuid4()
    user.email = email
    user.password = password
    user.first_name = first_name
    user.last_name = last_name
    user.is_active = is_active

    return user


def make_login_data(
    *,
    email="user@example.com",
    password="password123",
    remember_me=False,
):
    login_data = MagicMock()
    login_data.email = email
    login_data.password = password
    login_data.remember_me = remember_me

    return login_data


@pytest.mark.asyncio
@patch("app.services.auth_service.verify_password", new_callable=AsyncMock)
async def test_login_success(
    mock_verify_password,
    test_auth_service,
):
    user = make_user()
    user.hashed_password = "hashed-password"

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=user)

    mock_verify_password.return_value = True

    test_auth_service._issue_token_pair = AsyncMock(
        return_value=("access-token", "refresh-token", 604800)
    )

    login_data = make_login_data(
        email="user@example.com",
        password="password123",
        remember_me=False,
    )

    (
        response,
        access_token,
        refresh_token,
        refresh_max_age,
    ) = await test_auth_service.login(login_data)

    assert access_token == "access-token"
    assert refresh_token == "refresh-token"
    assert refresh_max_age == 604800

    test_auth_service.user_repo.get_by_email.assert_awaited_once_with(
        "user@example.com"
    )

    mock_verify_password.assert_awaited_once_with(
        "password123",
        "hashed-password",
    )

    test_auth_service._issue_token_pair.assert_awaited_once_with(
        user=user,
        refresh_expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
    )


@pytest.mark.asyncio
async def test_login_user_not_found(test_auth_service):

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=None)

    login_data = make_login_data(
        email="unknown@example.com",
        password="password123",
    )

    with pytest.raises(UnauthorizedException) as exc_info:
        await test_auth_service.login(login_data)

    assert exc_info.value.message == "Invalid email or password"

    test_auth_service.user_repo.get_by_email.assert_awaited_once_with(login_data.email)

    test_auth_service.user_repo.get_by_email.assert_awaited_once_with(
        "unknown@example.com"
    )


@pytest.mark.asyncio
async def test_login_user_exists_no_password(test_auth_service):

    user = make_user()
    user.hashed_password = None

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=user)

    login_data = make_login_data(
        email="user@example.com",
    )

    with pytest.raises(UnauthorizedException) as exc_info:
        await test_auth_service.login(login_data)

    assert exc_info.value.message == "Invalid email or password"

    test_auth_service.user_repo.get_by_email.assert_awaited_once_with(login_data.email)

    test_auth_service.db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.services.auth_service.verify_password", new_callable=AsyncMock)
async def test_login_user_exists_no_active(mock_verify_password, test_auth_service):

    user = make_user()
    user.is_active = False

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=user)
    mock_verify_password.return_value = True

    login_data = make_login_data(
        email="user@example.com",
        password="password123",
    )

    with pytest.raises(UnauthorizedException) as exc_info:
        await test_auth_service.login(login_data)

    assert exc_info.value.message == "Invalid email or password"

    test_auth_service.user_repo.get_by_email.assert_awaited_once_with(login_data.email)

    mock_verify_password.assert_awaited_once()
    test_auth_service.db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.services.auth_service.create_access_token")
@patch("app.services.auth_service.create_refresh_token")
@patch("app.services.auth_service.hash_refresh_token")
async def test_refresh_token_success(
    mock_hash_token,
    mock_create_refresh_token,
    mock_create_access_token,
    test_auth_service,
):
    user = make_user()
    user.id = uuid4()
    user.is_active = True
    now = utc_now()

    stored_token = MagicMock()
    stored_token.user_id = user.id
    stored_token.family_id = uuid4()
    stored_token.expires_at = now + timedelta(days=1)
    stored_token.is_revoked = False

    test_auth_service.refresh_token_repo.get_by_token_hash_for_update = AsyncMock(
        return_value=stored_token
    )
    test_auth_service.refresh_token_repo.create = AsyncMock()
    test_auth_service.user_repo.get_by_id = AsyncMock(return_value=user)

    test_auth_service.db.flush = AsyncMock()
    test_auth_service.db.rollback = AsyncMock()

    mock_hash_token.side_effect = [
        "old-token-hash",
        "new-token-hash",
    ]
    mock_create_refresh_token.return_value = "new-refresh-token"
    mock_create_access_token.return_value = "new-access-token"

    (
        result,
        access_token,
        refresh_token,
        max_age,
    ) = await test_auth_service.refresh_token("old-refresh-token")

    assert result.message == "Token refreshed successfully"
    assert access_token == "new-access-token"
    assert refresh_token == "new-refresh-token"
    assert max_age > 0

    assert stored_token.is_revoked is True
    test_auth_service.db.flush.assert_awaited_once()
    test_auth_service.refresh_token_repo.create.assert_awaited_once()
    test_auth_service.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_token_missing(test_auth_service):
    test_auth_service.db.rollback = AsyncMock()
    test_auth_service.refresh_token_repo.get_by_token_hash_for_update = AsyncMock(
        return_value=None
    )

    with pytest.raises(
        UnauthorizedException,
        match="Refresh token is required",
    ):
        await test_auth_service.refresh_token(None)

    test_auth_service.db.rollback.assert_awaited_once()
    test_auth_service.refresh_token_repo.get_by_token_hash_for_update.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.services.auth_service.hash_refresh_token")
async def test_refresh_token_invalid(
    mock_hash_token,
    test_auth_service,
):
    mock_hash_token.return_value = "unknown-token-hash"

    test_auth_service.refresh_token_repo.get_by_token_hash_for_update = AsyncMock(
        return_value=None
    )
    test_auth_service.db.rollback = AsyncMock()

    with pytest.raises(UnauthorizedException, match="Invalid refresh token"):
        await test_auth_service.refresh_token("invalid-refresh-token")

    mock_hash_token.assert_called_once_with("invalid-refresh-token")
    test_auth_service.refresh_token_repo.get_by_token_hash_for_update.assert_awaited_once_with(
        "unknown-token-hash"
    )
    test_auth_service.db.rollback.assert_awaited_once()
    test_auth_service.user_repo.get_by_id.assert_not_called()
    test_auth_service.refresh_token_repo.create.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.auth_service.hash_refresh_token")
async def test_refresh_token_expired(
    mock_hash_token,
    test_auth_service,
):
    mock_hash_token.return_value = "expired-token-hash"

    stored_token = MagicMock()
    stored_token.user_id = "user-123"
    stored_token.family_id = "family-123"
    stored_token.expires_at = utc_now() - timedelta(days=1)
    stored_token.is_revoked = False

    test_auth_service.refresh_token_repo.get_by_token_hash_for_update = AsyncMock(
        return_value=stored_token
    )
    test_auth_service.db.rollback = AsyncMock()

    with pytest.raises(
        UnauthorizedException,
        match="Refresh token has expired",
    ):
        await test_auth_service.refresh_token("expired-refresh-token")

    mock_hash_token.assert_called_once_with("expired-refresh-token")
    test_auth_service.refresh_token_repo.get_by_token_hash_for_update.assert_awaited_once_with(
        "expired-token-hash"
    )
    test_auth_service.db.rollback.assert_awaited_once()
    test_auth_service.user_repo.get_by_id.assert_not_called()
    test_auth_service.refresh_token_repo.create.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.auth_service.hash_refresh_token")
async def test_logout_current_device_success(
    mock_hash_token,
    test_auth_service,
):

    mock_hash_token.return_value = "hashed-refresh-token"

    stored_token = MagicMock()
    stored_token.user_id = "user-123"
    stored_token.family_id = "family-123"

    test_auth_service.refresh_token_repo.get_by_token_hash = AsyncMock(
        return_value=stored_token
    )
    test_auth_service.refresh_token_repo.revoke_family = AsyncMock()

    await test_auth_service.logout_current_device("refresh-token")

    mock_hash_token.assert_called_once_with("refresh-token")

    test_auth_service.refresh_token_repo.get_by_token_hash.assert_awaited_once_with(
        "hashed-refresh-token"
    )

    test_auth_service.refresh_token_repo.revoke_family.assert_awaited_once_with(
        "family-123"
    )


@pytest.mark.asyncio
@patch("app.services.auth_service.hash_refresh_token")
async def test_logout_current_device_unknown_token(
    mock_hash_token,
    test_auth_service,
):
    mock_hash_token.return_value = "unknown-refresh-token"

    stored_token = MagicMock()
    stored_token.user_id = "user-123"
    stored_token.family_id = "family-123"

    test_auth_service.refresh_token_repo.get_by_token_hash = AsyncMock(
        return_value=None
    )
    test_auth_service.refresh_token_repo.revoke_family = AsyncMock()

    result = await test_auth_service.logout_current_device("unknown-token")

    assert result is None

    test_auth_service.refresh_token_repo.get_by_token_hash.assert_awaited_once_with(
        "unknown-refresh-token"
    )

    test_auth_service.refresh_token_repo.revoke_family.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_all_device_success(
    test_auth_service,
):

    user_id = "user-123"

    test_auth_service.refresh_token_repo.revoke_all_for_user = AsyncMock()

    result = await test_auth_service.logout_all_devices(user_id)

    assert result is None

    test_auth_service.refresh_token_repo.revoke_all_for_user.assert_awaited_once_with(
        user_id
    )


@pytest.mark.asyncio
async def test_logout_all_device_unknown_user(
    test_auth_service,
):
    user_id = "unknown-user"

    test_auth_service.refresh_token_repo.revoke_all_for_user = AsyncMock()

    result = await test_auth_service.logout_all_devices(user_id)

    assert result is None

    test_auth_service.refresh_token_repo.revoke_all_for_user.assert_awaited_once_with(
        user_id
    )


@pytest.mark.asyncio
@patch("app.services.auth_service.enqueue_job", create=True)
@patch("app.services.auth_service.store_passcode", new_callable=AsyncMock)
@patch("app.services.auth_service.generate_passcode")
@patch("app.services.auth_service.check_passcode_request_limit", new_callable=AsyncMock)
async def test_request_passcode_success(
    mock_check_limit,
    mock_generate_passcode,
    mock_store_passcode,
    mock_enqueue_job,
    test_auth_service,
):
    from unittest.mock import AsyncMock, MagicMock

    email = "user@example.com"
    client_ip = "127.0.0.1"
    redis = AsyncMock()

    user = MagicMock()
    user.first_name = "John"

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=user)

    test_auth_service.arq_pool.enqueue_job = AsyncMock()

    mock_generate_passcode.return_value = "123456"

    await test_auth_service.request_passcode(
        email="  USER@EXAMPLE.COM  ",
        client_ip=client_ip,
        redis=redis,
    )

    test_auth_service.user_repo.get_by_email.assert_awaited_once_with(email)

    mock_check_limit.assert_awaited_once_with(
        redis=redis,
        email=email,
        client_ip=client_ip,
    )

    mock_generate_passcode.assert_called_once_with()

    mock_store_passcode.assert_awaited_once_with(
        redis=redis,
        email=email,
        passcode="123456",
    )

    redis.set.assert_awaited_once()

    test_auth_service.arq_pool.enqueue_job.assert_awaited_once()
    assert (
        test_auth_service.arq_pool.enqueue_job.await_args.args[0]
        == "send_passcode_email_job"
    )


@pytest.mark.asyncio
@patch(
    "app.services.auth_service.check_passcode_request_limit",
    new_callable=AsyncMock,
)
async def test_request_passcode_rate_limit_exceeded(
    mock_check_limit,
    test_auth_service,
):

    redis = AsyncMock()

    test_auth_service.user_repo.get_by_email = AsyncMock(
        return_value=MagicMock(first_name="John")
    )

    mock_check_limit.side_effect = TooManyRequestsException(
        message="Too many passcode requests"
    )

    with pytest.raises(TooManyRequestsException):
        await test_auth_service.request_passcode(
            email=" USER@EXAMPLE.COM ",
            client_ip="127.0.0.1",
            redis=redis,
        )

    mock_check_limit.assert_awaited_once_with(
        redis=redis,
        email="user@example.com",
        client_ip="127.0.0.1",
    )

    redis.set.assert_not_awaited()
    test_auth_service.arq_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_passcode_generation_failure(
    test_auth_service,
):
    redis = AsyncMock()

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=None)

    with (
        patch(
            "app.services.auth_service.check_passcode_request_limit",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.auth_service.generate_passcode",
            side_effect=RuntimeError("Generation failed"),
        ) as mock_generate,
        patch(
            "app.services.auth_service.store_passcode",
            new_callable=AsyncMock,
        ) as mock_store,
    ):
        with pytest.raises(RuntimeError, match="Generation failed"):
            await test_auth_service.request_passcode(
                email="user@example.com",
                client_ip="127.0.0.1",
                redis=redis,
            )

    mock_generate.assert_called_once()
    mock_store.assert_not_awaited()
    redis.set.assert_not_awaited()
    test_auth_service.arq_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_passcode_storage_failure(test_auth_service):
    redis = AsyncMock()

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=None)

    with (
        patch(
            "app.services.auth_service.check_passcode_request_limit",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.auth_service.generate_passcode",
            return_value="123456",
        ),
        patch(
            "app.services.auth_service.store_passcode",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Passcode storage failed"),
        ) as mock_store,
    ):
        with pytest.raises(
            RuntimeError,
            match="Passcode storage failed",
        ):
            await test_auth_service.request_passcode(
                email="user@example.com",
                client_ip="127.0.0.1",
                redis=redis,
            )

    mock_store.assert_awaited_once()
    redis.set.assert_not_awaited()
    test_auth_service.arq_pool.enqueue_job.assert_not_awaited()


@pytest.fixture
def passcode_mocks(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.services import auth_service as auth_service_module

    mocks = {
        "get_attempts": AsyncMock(return_value=0),
        "get_attempt_ttl": AsyncMock(return_value=60),
        "get_passcode": AsyncMock(return_value="stored-hash"),
        "verify_passcode": MagicMock(return_value=True),
        "increment_attempts": AsyncMock(return_value=1),
        "consume_passcode": AsyncMock(return_value=True),
        "reset_attempts": AsyncMock(),
    }

    monkeypatch.setattr(
        auth_service_module,
        "get_passcode_attempts",
        mocks["get_attempts"],
    )
    monkeypatch.setattr(
        auth_service_module,
        "get_passcode_attempt_ttl",
        mocks["get_attempt_ttl"],
    )
    monkeypatch.setattr(
        auth_service_module,
        "get_passcode",
        mocks["get_passcode"],
    )
    monkeypatch.setattr(
        auth_service_module,
        "verify_passcode",
        mocks["verify_passcode"],
    )
    monkeypatch.setattr(
        auth_service_module,
        "increment_passcode_attempts",
        mocks["increment_attempts"],
    )
    monkeypatch.setattr(
        auth_service_module,
        "consume_passcode",
        mocks["consume_passcode"],
    )
    monkeypatch.setattr(
        auth_service_module,
        "reset_passcode_attempts",
        mocks["reset_attempts"],
    )

    return mocks


@pytest.mark.asyncio
async def test_verify_email_passcode_success(
    test_auth_service,
    passcode_mocks,
):
    from unittest.mock import AsyncMock, MagicMock

    redis = AsyncMock()
    user = MagicMock()
    user.is_active = True

    test_auth_service.user_repo.get_by_email = AsyncMock(return_value=user)
    test_auth_service._issue_token_pair = AsyncMock(
        return_value=("access-token", "refresh-token", 3600)
    )

    (
        result,
        access_token,
        refresh_token,
        max_age,
    ) = await test_auth_service.verify_email_passcode(
        email="  USER@EXAMPLE.COM ",
        passcode="123456",
        redis=redis,
    )

    assert result.message == "Login successful"
    assert access_token == "access-token"
    assert refresh_token == "refresh-token"
    assert max_age == 3600

    test_auth_service.user_repo.get_by_email.assert_awaited_once_with(
        "user@example.com"
    )
    passcode_mocks["get_attempts"].assert_awaited_once_with(
        redis=redis,
        email="user@example.com",
    )
    passcode_mocks["verify_passcode"].assert_called_once_with(
        "123456",
        "stored-hash",
    )
    passcode_mocks["consume_passcode"].assert_awaited_once_with(
        redis=redis,
        email="user@example.com",
        expected_hash="stored-hash",
    )
    passcode_mocks["reset_attempts"].assert_awaited_once_with(
        redis=redis,
        email="user@example.com",
    )
    test_auth_service.user_repo.create.assert_not_called()
    test_auth_service._issue_token_pair.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.services.auth_service.generate_oauth_state")
async def test_start_oauth_success(
    mock_generate_oauth_state,
    test_auth_service,
):

    provider = "google"
    state = "test-oauth-state"

    config = replace(
        OAUTH_PROVIDERS[provider],
        client_secret="test-client-secret",
    )

    with patch(
        "app.services.auth_service.OAUTH_PROVIDERS",
        {provider: config},
    ):
        mock_generate_oauth_state.return_value = state

        config = OAUTH_PROVIDERS[provider]

        test_auth_service.oauth_state_repo.create = AsyncMock()

        authorization_url = await test_auth_service.start_oauth(provider)

    mock_generate_oauth_state.assert_called_once_with()

    test_auth_service.oauth_state_repo.create.assert_awaited_once_with(
        state=state,
        provider=provider,
        ttl=settings.OAUTH_STATE_EXPIRE_SECONDS,
    )

    parsed_url = urlparse(authorization_url)
    query_params = parse_qs(parsed_url.query)

    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == urlparse(config.authorization_url).netloc
    assert query_params["client_id"] == [config.client_id]
    assert query_params["redirect_uri"] == [config.redirect_uri]
    assert query_params["response_type"] == ["code"]
    assert query_params["state"] == [state]


@pytest.mark.asyncio
async def test_start_oauth_unsupported_provider(test_auth_service):
    with pytest.raises(NotFoundException) as exc_info:
        await test_auth_service.start_oauth("unsupported_provider")

    assert str(exc_info.value) == "OAuth provider not supported"

    test_auth_service.oauth_state_repo.create.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.auth_service.OAUTH_PROVIDERS")
async def test_start_oauth_provider_not_configured(
    mock_oauth_providers,
    test_auth_service,
):
    mock_oauth_providers.get.return_value = SimpleNamespace(
        client_secret=None,
    )

    with pytest.raises(ServiceUnavailableException) as exc_info:
        await test_auth_service.start_oauth("google")

    assert str(exc_info.value) == ("OAuth provider is temporarily unavailable")

    test_auth_service.oauth_state_repo.create.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.auth_service.generate_oauth_state")
async def test_start_oauth_state_storage_failure(
    mock_generate_oauth_state,
    test_auth_service,
):
    mock_generate_oauth_state.return_value = "test-oauth-state"

    test_auth_service.oauth_state_repo.create = AsyncMock(
        side_effect=RuntimeError("Redis unavailable")
    )

    with pytest.raises(RuntimeError, match="Redis unavailable"):
        await test_auth_service.start_oauth("google")

    test_auth_service.oauth_state_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_oauth_callback_invalid_state(test_auth_service):
    test_auth_service.oauth_state_repo.consume = AsyncMock(return_value=None)

    with pytest.raises(UnauthorizedException):
        await test_auth_service.oauth_callback(
            provider="google",
            code="test-authorization-code",
            state="invalid-state",
        )

    test_auth_service.oauth_state_repo.consume.assert_awaited_once_with("invalid-state")


@pytest.mark.asyncio
async def test_oauth_callback_provider_mismatch(test_auth_service):
    test_auth_service.oauth_state_repo.consume = AsyncMock(return_value="github")

    with pytest.raises(UnauthorizedException):
        await test_auth_service.oauth_callback(
            provider="google",
            code="test-authorization-code",
            state="valid-state",
        )

    test_auth_service.oauth_state_repo.consume.assert_awaited_once_with("valid-state")
