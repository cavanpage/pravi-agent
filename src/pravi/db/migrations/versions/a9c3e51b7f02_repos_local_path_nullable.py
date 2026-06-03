"""repos.local_path nullable

Revision ID: a9c3e51b7f02
Revises: f5b8a2c3d4e7
Create Date: 2026-05-26 20:00:00.000000

See ADR 0003: with the sandbox seam, the canonical Repo identity is its
GitHub coordinates. `local_path` becomes a sandbox-internal detail (where
the local backend puts its clone) and is populated lazily on first
provision. New GitHub-imported repos start with NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9c3e51b7f02'
down_revision: Union[str, None] = 'f5b8a2c3d4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'repos',
        'local_path',
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Existing rows may already have NULL — backfill to empty string before
    # restoring NOT NULL so the migration is reversible without data loss.
    op.execute("UPDATE repos SET local_path = '' WHERE local_path IS NULL")
    op.alter_column(
        'repos',
        'local_path',
        existing_type=sa.Text(),
        nullable=False,
    )
