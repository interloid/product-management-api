from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token_model import RefreshToken


class RefreshTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, refresh_token: RefreshToken) -> RefreshToken:

        self.db.add(refresh_token)
        await self.db.flush()
        await self.db.refresh(refresh_token)
        return refresh_token

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_token_hash_for_update(
        self,
        token_hash: str,
    ) -> RefreshToken | None:
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_by_family_id(self, family_id: UUID) -> list[RefreshToken]:

        stmt = (
            select(RefreshToken)
            .where(RefreshToken.family_id == family_id)
            .order_by(RefreshToken.created_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def revoke_family(self, family_id: UUID) -> None:

        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.is_revoked.is_(False),
            )
            .values(is_revoked=True)
        )
        await self.db.execute(stmt)

    async def revoke_all_for_user(self, user_id: UUID) -> None:

        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.is_revoked.is_(False))
            .values(is_revoked=True)
        )
        await self.db.execute(stmt)

    async def delete_expired(self, now: datetime) -> int:

        stmt = delete(RefreshToken).where(
            RefreshToken.expires_at <= now,
        )
        result = await self.db.execute(stmt)
        return result.rowcount or 0
