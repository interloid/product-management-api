from uuid import UUID

from pydantic import EmailStr, Field

from app.core.constants import RoleEnum
from app.schemas.common import BaseSchema


class UserBase(BaseSchema):
    email: EmailStr


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserResponse(UserBase):
    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    avatar_url: str | None = None
    is_active: bool
    role: RoleEnum
