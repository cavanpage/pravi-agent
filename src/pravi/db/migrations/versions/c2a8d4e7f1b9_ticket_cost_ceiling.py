"""tickets.cost_ceiling_usd — per-task cumulative spend cap

Nullable: null means inherit from parent, then fall back to env default,
then unlimited. Applied at all three levels (epic/feature/task) so a single
limit at the epic level can constrain every descendant.

Revision ID: c2a8d4e7f1b9
Revises: 461d70ada9af
Create Date: 2026-05-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2a8d4e7f1b9"
down_revision: Union[str, None] = "461d70ada9af"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("cost_ceiling_usd", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "cost_ceiling_usd")
