"""add avatar url to users

Revision ID: 06240db0f3e2
Revises: 9b831f7f58a3
Create Date: 2026-09-17 15:31:58.320222

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '06240db0f3e2'
down_revision: Union[str, None] = '9b831f7f58a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "avatar_url",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "users",
        "avatar_url",
    )
    
