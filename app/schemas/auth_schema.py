import re
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import BaseSchema
from app.schemas.user_schema import UserResponse


def validate_password(password: str) -> str:

    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")

    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")

    if not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one number")

    return password


class LoginRequest(BaseSchema):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    remember_me: bool = False

    @field_validator("password")
    @classmethod
    def validate(cls, value: str) -> str:
        return validate_password(value)


class PasscodeRequest(BaseSchema):
    email: EmailStr


class LoginResponse(BaseSchema):
    access_token: str
    expires_in: int
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse


class PasscodeVerifyRequest(BaseSchema):
    email: EmailStr
    passcode: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
    )


class AccessTokenPayload(BaseSchema):
    sub: UUID
    type: Literal["access"]
    exp: int


class TokenResponse(BaseSchema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0, description="Access token lifetime in seconds")
