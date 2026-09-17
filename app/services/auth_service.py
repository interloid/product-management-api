import json
from datetime import timedelta
from typing import NoReturn
from urllib.parse import urlencode
from uuid import UUID, uuid4

from arq.connections import ArqRedis
from authlib.integrations.httpx_client import AsyncOAuth2Client
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.oauth.client import get_oauth_client
from app.core.oauth.config import OAUTH_PROVIDERS
from app.core.oauth.state import generate_oauth_state
from app.core.passcode import (
    check_passcode_request_limit,
    consume_passcode,
    generate_passcode,
    get_passcode,
    get_passcode_attempt_ttl,
    get_passcode_attempts,
    increment_passcode_attempts,
    reset_passcode_attempts,
    store_passcode,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    verify_passcode,
    verify_password,
)
from app.core.settings import settings
from app.exceptions.custom import (
    AppException,
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    TooManyRequestsException,
    UnauthorizedException,
)
from app.models.refresh_token_model import RefreshToken
from app.models.user_identity_model import UserIdentity
from app.models.user_model import User
from app.repositories import (
    OAuthStateRepository,
    RefreshTokenRepository,
    UserIdentityRepository,
    UserRepository,
)
from app.schemas.auth_schema import (
    LoginRequest,
)
from app.schemas.response import ApiResponse
from app.utils.helpers import utc_now

logger = get_logger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession, redis: Redis, arq_pool: ArqRedis):
        self.db = db
        self.arq_pool = arq_pool
        self.user_repo = UserRepository(db)
        self.refresh_token_repo = RefreshTokenRepository(db)
        self.user_identity_repo = UserIdentityRepository(db)
        self.oauth_state_repo = OAuthStateRepository(redis)

    async def _rollback(self) -> None:
        await self.db.rollback()

    async def _create_refresh_credential(
        self,
        *,
        user: User,
        expire_days: int,
    ) -> tuple[str, int]:
        raw_refresh_token = create_refresh_token()

        refresh_token_record = RefreshToken(
            user_id=user.id,
            family_id=uuid4(),
            token_hash=hash_refresh_token(raw_refresh_token),
            expires_at=utc_now() + timedelta(days=expire_days),
            is_revoked=False,
        )

        await self.refresh_token_repo.create(refresh_token_record)

        refresh_max_age = expire_days * 24 * 60 * 60
        return raw_refresh_token, refresh_max_age

    async def _issue_token_pair(
        self,
        *,
        user: User,
        refresh_expire_days: int,
    ) -> tuple[str, str, int]:

        access_token = create_access_token({"sub": str(user.id)})

        (
            raw_refresh_token,
            refresh_max_age,
        ) = await self._create_refresh_credential(
            user=user,
            expire_days=refresh_expire_days,
        )

        return access_token, raw_refresh_token, refresh_max_age

    async def login(
        self,
        login_data: LoginRequest,
    ) -> tuple[ApiResponse[None], str, str, int]:

        try:
            user = await self.user_repo.get_by_email(login_data.email)
            if user is None:
                logger.warning("Invalid email or password | email=%s", login_data.email)
                raise UnauthorizedException(message="Invalid email or password")

            if user.hashed_password is None:
                logger.warning("Invalid email or password | email=%s", login_data.email)
                raise UnauthorizedException(message="Invalid email or password")

            if not await verify_password(login_data.password, user.hashed_password):
                logger.warning("Invalid email or password | email=%s", login_data.email)
                raise UnauthorizedException(message="Invalid email or password")

            if not user.is_active:
                logger.warning("Invalid email or password | email=%s", login_data.email)
                raise UnauthorizedException(message="Invalid email or password")

            expire_days = (
                settings.REMEMBER_ME_EXPIRE_DAYS
                if login_data.remember_me
                else settings.REFRESH_TOKEN_EXPIRE_DAYS
            )

            (
                access_token,
                raw_refresh_token,
                refresh_max_age,
            ) = await self._issue_token_pair(
                user=user,
                refresh_expire_days=expire_days,
            )

            logger.info("User logged in Successfully | user_id=%s", user.id)

        except AppException:
            await self._rollback()
            raise

        except Exception:
            await self._rollback()
            logger.exception("Unexpected error")
            raise

        result = ApiResponse[None](message="Login successful")
        return result, access_token, raw_refresh_token, refresh_max_age

    async def _handle_refresh_token(
        self,
        stored_token: RefreshToken,
    ) -> NoReturn:
        logger.warning(
            "Refresh token reuse detected | user_id=%s | family_id=%s",
            stored_token.user_id,
            stored_token.family_id,
        )

        await self.refresh_token_repo.revoke_family(
            stored_token.family_id,
        )
        await self.db.commit()
        raise UnauthorizedException(message="Invalid refresh token")

    async def refresh_token(
        self,
        raw_refresh_token: str | None,
    ) -> tuple[ApiResponse[None], str, str, int]:

        try:
            if raw_refresh_token is None:
                logger.warning("Refresh token is required")
                raise UnauthorizedException(message="Refresh token is required")

            now = utc_now()

            token_hash = hash_refresh_token(raw_refresh_token)

            stored_token = await self.refresh_token_repo.get_by_token_hash_for_update(
                token_hash
            )

            if stored_token is None:
                logger.warning("Unknown refresh token presented")
                raise UnauthorizedException(message="Invalid refresh token")

            if stored_token.expires_at <= now:
                logger.warning(
                    "Expired refresh token presented  | user_id=%s | family_id=%s",
                    stored_token.user_id,
                    stored_token.family_id,
                )
                raise UnauthorizedException(message="Refresh token has expired")

            if stored_token.is_revoked:
                await self._handle_refresh_token(stored_token)

            user = await self.user_repo.get_by_id(stored_token.user_id)

            if user is None or not user.is_active:
                await self.refresh_token_repo.revoke_all_for_user(stored_token.user_id)
                await self.db.commit()
                raise UnauthorizedException(message="Invalid refresh token")

            stored_token.is_revoked = True
            await self.db.flush()

            new_raw_refresh_token = create_refresh_token()

            new_token_hash = hash_refresh_token(new_raw_refresh_token)

            new_refresh_token = RefreshToken(
                user_id=user.id,
                family_id=stored_token.family_id,
                token_hash=new_token_hash,
                expires_at=stored_token.expires_at,
                is_revoked=False,
            )

            await self.refresh_token_repo.create(new_refresh_token)

            access_token = create_access_token({"sub": str(user.id)})

            remaining_seconds = max(
                1,
                int((stored_token.expires_at - now).total_seconds()),
            )

            logger.info(
                "Refresh token rotated successfully | user_id=%s | family_id=%s",
                user.id,
                stored_token.family_id,
            )

            result = ApiResponse[None](
                message="Token refreshed successfully",
                data=None,
            )

            return (
                result,
                access_token,
                new_raw_refresh_token,
                remaining_seconds,
            )

        except AppException:
            await self._rollback()
            raise

        except Exception:
            await self._rollback()
            logger.exception(
                "Unexpected error while refreshing token",
            )
            raise

    async def logout_current_device(self, raw_refresh_token: str) -> None:

        try:
            if raw_refresh_token is None:
                logger.info("Logout requested without a refresh token")
                return

            token_hash = hash_refresh_token(raw_refresh_token)

            stored_token = await self.refresh_token_repo.get_by_token_hash(token_hash)

            if stored_token is None:
                logger.info("Logout requested with an unknown refresh token")
                return

            await self.refresh_token_repo.revoke_family(stored_token.family_id)
            logger.info(
                "User logged out successfully | user_id=%s | family_id=%s",
                stored_token.user_id,
                stored_token.family_id,
            )

        except AppException:
            await self._rollback()
            raise

        except Exception:
            await self._rollback()
            logger.exception("Unexpected error while logging out")
            raise

    async def logout_all_devices(self, user_id: UUID) -> None:

        try:
            if user_id is None:
                logger.info("Logout requested without a refresh token")
                return

            await self.refresh_token_repo.revoke_all_for_user(user_id)
            logger.info(
                "User logged out successfully | user_id=%s",
                user_id,
            )

        except AppException:
            await self._rollback()
            raise

        except Exception:
            await self._rollback()
            logger.exception("Unexpected error while logging out")
            raise

    async def request_passcode(
        self,
        *,
        email: str,
        client_ip: str,
        redis: Redis,
    ) -> None:

        email = email.strip().lower()

        user = await self.user_repo.get_by_email(email)

        if user is not None and not user.is_active:
            logger.warning("Invalid email passcode verification attempt")
            raise UnauthorizedException(
                message="Invalid email passcode verification attempt.",
            )

        await check_passcode_request_limit(
            redis=redis,
            email=email,
            client_ip=client_ip,
        )

        passcode = generate_passcode()

        await store_passcode(
            redis=redis,
            email=email,
            passcode=passcode,
        )

        email_job_id = uuid4().hex
        email_job_key = f"jobs:passcode-email:{email_job_id}"

        email_data = {
            "to_email": email,
            "first_name": user.first_name if user else "User",
            "passcode": passcode,
            "expiry_minutes": settings.PASSCODE_EXPIRE_SECONDS // 60,
        }

        await redis.set(
            email_job_key,
            json.dumps(email_data),
            ex=settings.PASSCODE_EXPIRE_SECONDS,
        )

        await self.arq_pool.enqueue_job(
            "send_passcode_email_job",
            email_job_id,
            _expires=settings.PASSCODE_EXPIRE_SECONDS,
        )

    async def verify_email_passcode(
        self,
        *,
        email: str,
        passcode: str,
        redis: Redis,
    ) -> tuple[ApiResponse[None], str, str, int]:

        try:
            email = email.strip().lower()

            user = await self.user_repo.get_by_email(email)

            if user is not None and not user.is_active:
                logger.warning("Invalid email passcode verification attempt")

                raise UnauthorizedException(
                    message="Invalid or expired passcode.",
                )

            attempts = await get_passcode_attempts(
                redis=redis,
                email=email,
            )

            if attempts >= settings.PASSCODE_MAX_ATTEMPTS:
                retry_after_seconds = await get_passcode_attempt_ttl(
                    redis=redis,
                    email=email,
                )

                logger.warning(
                    "Passcode verification attempts exceeded | email=%s",
                    email,
                )

                raise TooManyRequestsException(
                    message="Too many attempts. Request a new passcode.",
                    details={
                        "attempts_used": attempts,
                        "max_attempts": settings.PASSCODE_MAX_ATTEMPTS,
                        "remaining_attempts": 0,
                        "retry_after_seconds": retry_after_seconds,
                    },
                )

            stored_hash = await get_passcode(
                redis=redis,
                email=email,
            )

            if stored_hash is None:
                logger.warning("Invalid or expired passcode")
                raise UnauthorizedException(
                    message="Invalid or expired passcode.",
                )

            if not verify_passcode(
                passcode,
                stored_hash,
            ):
                attempts = await increment_passcode_attempts(
                    redis=redis,
                    email=email,
                )

                remaining_attempts = max(
                    settings.PASSCODE_MAX_ATTEMPTS - attempts,
                    0,
                )
                retry_after_seconds = await get_passcode_attempt_ttl(
                    redis=redis,
                    email=email,
                )

                details = {
                    "attempts_used": attempts,
                    "max_attempts": settings.PASSCODE_MAX_ATTEMPTS,
                    "remaining_attempts": remaining_attempts,
                    "retry_after_seconds": retry_after_seconds,
                }

                if attempts >= settings.PASSCODE_MAX_ATTEMPTS:
                    logger.warning(
                        "Passcode verification attempts exceeded | email=%s",
                        email,
                    )
                    raise TooManyRequestsException(
                        message="Too many attempts. Request a new passcode.",
                        details=details,
                    )

                logger.warning("Invalid or expired passcode")
                raise UnauthorizedException(
                    message="Invalid or expired passcode.",
                    details=details,
                )

            if not await consume_passcode(
                redis=redis,
                email=email,
                expected_hash=stored_hash,
            ):
                raise UnauthorizedException(
                    message="Invalid or expired passcode.",
                )

            await reset_passcode_attempts(
                redis=redis,
                email=email,
            )
            if user is None:
                user = User(
                    email=email,
                    first_name="User",
                    last_name="",
                    is_active=True,
                )

                await self.user_repo.create(user)
            logger.info("User logged in Successfully | email=%s", email)

            (
                access_token,
                raw_refresh_token,
                refresh_max_age,
            ) = await self._issue_token_pair(
                user=user,
                refresh_expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
            )

        except AppException:
            await self._rollback()
            raise

        except Exception:
            await self._rollback()

            logger.exception("Unexpected error during email passcode verification")

            raise

        return (
            ApiResponse[None](
                message="Login successful",
                data=None,
            ),
            access_token,
            raw_refresh_token,
            refresh_max_age,
        )

    async def start_oauth(self, provider: str) -> str:

        try:
            config = OAUTH_PROVIDERS.get(provider)

            if config is None:
                logger.warning(
                    "Unsupported OAuth provider | provider=%s",
                    provider,
                )
                raise NotFoundException(
                    message="OAuth provider not supported",
                )

            if config.client_secret is None:
                logger.error(
                    "OAuth provider is not configured | provider=%s",
                    provider,
                )
                raise ServiceUnavailableException(
                    message="OAuth provider is temporarily unavailable",
                )

            state = generate_oauth_state()

            await self.oauth_state_repo.create(
                state=state,
                provider=provider,
                ttl=settings.OAUTH_STATE_EXPIRE_SECONDS,
            )

            params = {
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "response_type": "code",
                "scope": " ".join(config.scopes),
                "state": state,
            }

            authorization_url = f"{config.authorization_url}?{urlencode(params)}"

            logger.info(
                "OAuth authorization started | provider=%s",
                provider,
            )

            return authorization_url

        except AppException:
            raise

        except Exception:
            logger.exception(
                "Unexpected error while starting OAuth | provider=%s",
                provider,
            )
            raise

    async def validate_oauth_state(
        self,
        provider: str,
        state: str,
    ) -> None:
        stored_provider = await self.oauth_state_repo.consume(state)

        if stored_provider is None:
            logger.warning(
                "Invalid or expired OAuth state | provider=%s",
                provider,
            )
            raise UnauthorizedException(
                message="Invalid or expired OAuth state",
            )

        if stored_provider != provider:
            logger.warning(
                "OAuth provider mismatch | expected=%s actual=%s",
                stored_provider,
                provider,
            )
            raise UnauthorizedException(
                message="Invalid OAuth state",
            )

    async def oauth_callback(
        self,
        provider: str,
        code: str,
        state: str,
    ) -> tuple[ApiResponse[None], str, str, int]:
        try:
            await self.validate_oauth_state(
                provider=provider,
                state=state,
            )

            config = OAUTH_PROVIDERS.get(provider)

            if config is None:
                logger.warning("OAuth provider not supported | provider=%s", provider)
                raise NotFoundException(
                    message="OAuth provider not supported",
                )

            if config.client_secret is None:
                logger.error(
                    "OAuth provider is not configured | provider=%s",
                    provider,
                )
                raise ServiceUnavailableException(
                    message="OAuth provider is temporarily unavailable",
                )

            async with get_oauth_client(provider) as client:
                logger.info(
                    "Exchanging OAuth code for token | provider=%s",
                    provider,
                )

                await client.fetch_token(
                    url=config.token_url,
                    code=code,
                    redirect_uri=config.redirect_uri,
                )

                logger.info(
                    "OAuth token exchange successful | provider=%s",
                    provider,
                )

                userinfo_response = await client.get(config.userinfo_url)

                logger.info(
                    "OAuth userinfo response | provider=%s status=%s",
                    provider,
                    userinfo_response.status_code,
                )

                userinfo_response.raise_for_status()

                userinfo = userinfo_response.json()

                if provider == "github":
                    email = await self.get_github_email(client)

            if provider == "github":
                provider_user_id = str(userinfo["id"])

                avatar_url = userinfo.get("avatar_url")

                full_name = userinfo.get("name") or userinfo.get("login", "")
                name_parts = full_name.split(maxsplit=1)

                first_name = name_parts[0] if name_parts else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""

            elif provider == "google":
                provider_user_id = userinfo["sub"]

                email = userinfo.get("email")

                avatar_url = userinfo.get("picture")

                if not email:
                    logger.warning(
                        "Google account does not provide email | email=%s", email
                    )
                    raise UnauthorizedException(
                        message="Google account does not provide an email",
                    )

                if userinfo.get("email_verified") is not True:
                    logger.warning("Google email is not verified | email=%s", email)
                    raise UnauthorizedException(
                        message="Google email is not verified",
                    )

                first_name = userinfo.get("given_name", "")
                last_name = userinfo.get("family_name", "")

            elif provider == "microsoft":
                provider_user_id = userinfo["sub"]

                email = userinfo.get("email")

                avatar_url = None

                if not email:
                    logger.warning(
                        "Microsoft account does not provide an email| email=%s", email
                    )
                    raise UnauthorizedException(
                        message="Microsoft account does not provide an email",
                    )

                full_name = userinfo.get("name", "")
                name_parts = full_name.split(maxsplit=1)

                first_name = name_parts[0] if name_parts else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""

            else:
                logger.warning("OAuth provider not supported")
                raise NotFoundException(
                    message="OAuth provider not supported",
                )

            identity = await self.user_identity_repo.get_by_provider_identity(
                provider=provider,
                provider_user_id=provider_user_id,
            )

            if identity is not None:
                user = await self.user_repo.get_by_id(identity.user_id)

                if user is None:
                    logger.warning(
                        "User associated with OAuth identity not found| user=%s", user
                    )
                    raise NotFoundException(
                        message="User associated with OAuth identity not found",
                    )

                if user.avatar_url is None and avatar_url is not None:
                    user.avatar_url = avatar_url
                    await self.db.flush()

            else:
                user = await self.user_repo.get_by_email(email)

                if user is not None:
                    logger.warning(
                        "An account with this email already exists| user=%s", user
                    )
                    raise ConflictException(
                        message=(
                            "An account with this email already exists. "
                            "Kindly sign in with the existing account."
                        ),
                    )

                user = User(
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    avatar_url=avatar_url,
                )

                user = await self.user_repo.create(user)

                identity = UserIdentity(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    email=email,
                )

                await self.user_identity_repo.create(identity)

            if not user.is_active:
                logger.warning("User account is inactive| user=%s", user)
                raise UnauthorizedException(message="User account is inactive")

            logger.info(
                "OAuth user info | provider=%s provider_user_id=%s email=%s",
                provider,
                provider_user_id,
                email,
            )

            logger.info(
                "OAuth avatar | provider=%s user_id=%s "
                "provider_avatar_present=%s stored_avatar_present=%s",
                provider,
                user.id,
                bool(avatar_url),
                bool(user.avatar_url),
            )

            (
                access_token,
                raw_refresh_token,
                refresh_max_age,
            ) = await self._issue_token_pair(
                user=user,
                refresh_expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
            )

            return (
                ApiResponse[None](
                    message=f"{provider.capitalize()} authentication successful",
                    data=None,
                ),
                access_token,
                raw_refresh_token,
                refresh_max_age,
            )

        except AppException:
            await self._rollback()
            raise

        except Exception:
            await self._rollback()

            logger.exception(
                "Unexpected OAuth callback error | provider=%s",
                provider,
            )

            raise

    async def get_github_email(self, client: AsyncOAuth2Client) -> str:
        response = await client.get(
            "https://api.github.com/user/emails",
            headers={
                "Accept": "application/vnd.github+json",
            },
        )

        response.raise_for_status()

        emails = response.json()

        for email_data in emails:
            if email_data.get("primary") and email_data.get("verified"):
                return email_data["email"]

        for email_data in emails:
            if email_data.get("verified"):
                return email_data["email"]

        logger.warning("No verified email found for GitHub account | emails=%s", emails)

        raise UnauthorizedException(
            message="No verified email found for GitHub account",
        )
