"""tickets.{clarify,decompose,draft,dev}_model — per-ticket per-stage model pins

Four nullable columns so a ticket can pin a specific Claude model per
stage. Null = inherit from parent → env default → SDK default. Applied
at all three levels (epic/feature/task) so a single set on the epic can
constrain every descendant.

Revision ID: f9d3e2c7b4a1
Revises: e7a4c1b9d3f2
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9d3e2c7b4a1"
down_revision: Union[str, None] = "e7a4c1b9d3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = ("clarify_model", "decompose_model", "draft_model", "dev_model")


def upgrade() -> None:
    for col in COLUMNS:
        op.add_column("tickets", sa.Column(col, sa.String(length=128), nullable=True))


def downgrade() -> None:
    for col in COLUMNS:
        op.drop_column("tickets", col)
