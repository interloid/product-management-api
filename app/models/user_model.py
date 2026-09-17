from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import RoleEnum
from app.db import BaseEntity

if TYPE_CHECKING:
    from app.models.refresh_token_model import RefreshToken
    from app.models.user_identity_model import UserIdentity


class User(BaseEntity):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    hashed_password: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    first_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    last_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    phone_number: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    avatar_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    role: Mapped[RoleEnum] = mapped_column(
        SQLEnum(
            RoleEnum,
            name="user_role",
            values_callable=lambda role_enum: [role.value for role in role_enum],
        ),
        nullable=False,
        default=RoleEnum.VIEWER,
        server_default=RoleEnum.VIEWER.value,
        index=True,
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    identities: Mapped[list["UserIdentity"]] = relationship(
        "UserIdentity",
        back_populates="user",
        cascade="all, delete-orphan",
    )
