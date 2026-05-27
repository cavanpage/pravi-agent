"""ticket github_issue_url

Revision ID: f5b8a2c3d4e7
Revises: e3a7f9c8b1d2
Create Date: 2026-05-26 19:00:00.000000

Records the source GitHub issue when a ticket was imported from one (via
the /issues page). Lets the UI render a "from GitHub #N" chip linking
back. Nullable — tickets created any other way leave it null.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5b8a2c3d4e7'
down_revision: Union[str, None] = 'e3a7f9c8b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tickets',
        sa.Column('github_issue_url', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tickets', 'github_issue_url')
