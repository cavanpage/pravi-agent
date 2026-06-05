"""cloudflare_connections table

Revision ID: d8f1a2b3c5e4
Revises: c4e2b8d6f1a9
Create Date: 2026-06-04 19:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f1a2b3c5e4"
down_revision: Union[str, None] = "c4e2b8d6f1a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloudflare_connections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("api_token", sa.Text(), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("token_id", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cloudflare_connections_revoked_at_id",
        "cloudflare_connections",
        ["revoked_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cloudflare_connections_revoked_at_id",
        table_name="cloudflare_connections",
    )
    op.drop_table("cloudflare_connections")
