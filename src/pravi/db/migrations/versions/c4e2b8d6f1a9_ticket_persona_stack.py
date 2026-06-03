"""ticket persona + stack

Revision ID: c4e2b8d6f1a9
Revises: a9c3e51b7f02
Create Date: 2026-06-03 13:00:00.000000

See ADR 0004 — agent personas (with stack specializations). Two
orthogonal nullable text columns: the decompose architect assigns,
the dev agent's system prompt is parameterized by both.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e2b8d6f1a9'
down_revision: Union[str, None] = 'a9c3e51b7f02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tickets',
        sa.Column('persona', sa.String(length=32), nullable=True),
    )
    op.add_column(
        'tickets',
        sa.Column('stack', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tickets', 'stack')
    op.drop_column('tickets', 'persona')
