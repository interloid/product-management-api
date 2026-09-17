from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Product Management System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    DATABASE_URL: SecretStr

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    SECRET_KEY: SecretStr
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    ACCESS_TOKEN_COOKIE_NAME: str = "access_token"
    REFRESH_TOKEN_COOKIE_NAME: str = "refresh_token"

    SESSION_EXPIRE_DAYS: int = 7
    REMEMBER_ME_EXPIRE_DAYS: int = 30

    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: SecretStr | None = None
    GOOGLE_REDIRECT_URI: str

    MICROSOFT_CLIENT_ID: str
    MICROSOFT_CLIENT_SECRET: SecretStr | None = None
    MICROSOFT_REDIRECT_URI: str
    MICROSOFT_TENANT_ID: str

    GITHUB_CLIENT_ID: str
    GITHUB_CLIENT_SECRET: SecretStr | None = None
    GITHUB_REDIRECT_URI: str

    OAUTH_STATE_EXPIRE_SECONDS: int = 600

    REDIS_URL: str | None = None
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_USERNAME: str
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int = 0

    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    AWS_REGION: str
    S3_BUCKET_NAME: str
    CLOUDFRONT_BASE_URL: AnyHttpUrl

    SMTP_HOST: str
    SMTP_PORT: int = 587
    SMTP_USERNAME: str
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_FROM_EMAIL: str
    SMTP_FROM_NAME: str = "Product Management System"
    SMTP_START_TLS: bool = True

    PASSCODE_PEPPER: SecretStr
    REFRESH_TOKEN_PEPPER: SecretStr

    PASSCODE_EXPIRE_SECONDS: int = 300
    PASSCODE_LENGTH: int = 6
    PASSCODE_MAX_ATTEMPTS: int = 3

    PASSCODE_REQUEST_EMAIL_LIMIT: int = 3
    PASSCODE_REQUEST_IP_LIMIT: int = 10
    PASSCODE_REQUEST_WINDOW_SECONDS: int = 900

    CORS_ORIGINS: str
    YOUR_REACT_URL: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("SECRET_KEY", "PASSCODE_PEPPER", "REFRESH_TOKEN_PEPPER")
    @classmethod
    def validate(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("Security secrets must contain at least 32 characters")
        return value

    @field_validator("CLOUDFRONT_BASE_URL", mode="after")
    @classmethod
    def validate_cloudfront_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:

        if value.scheme != "https":
            raise ValueError("CLOUDFRONT BASE URL must use HTTPS")

        if value.query is not None:
            raise ValueError(
                "CLOUDFRONT BASE URL must not contain query parameters",
            )

        if value.fragment is not None:
            raise ValueError(
                "CLOUDFRONTBASE URL must not contain a URL fragment",
            )

        return value


settings = Settings()
