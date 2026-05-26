"""ticket hierarchy: kind + parent_id

Revision ID: 461d70ada9af
Revises: a1f3c9b2d4e6
Create Date: 2026-05-25 01:31:52.979732

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '461d70ada9af'
down_revision: Union[str, None] = 'a1f3c9b2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add `kind` with a server-default so existing rows backfill to 'task'.
    # The default is then dropped — application code is responsible for
    # supplying it on new rows.
    op.add_column(
        'tickets',
        sa.Column(
            'kind',
            sa.String(length=16),
            nullable=False,
            server_default='task',
        ),
    )
    op.alter_column('tickets', 'kind', server_default=None)

    op.add_column('tickets', sa.Column('parent_id', sa.BigInteger(), nullable=True))
    op.create_index('ix_tickets_parent_id', 'tickets', ['parent_id'], unique=False)
    op.create_index('ix_tickets_repo_id_kind', 'tickets', ['repo_id', 'kind'], unique=False)
    op.create_foreign_key(
        'fk_tickets_parent_id',
        'tickets',
        'tickets',
        ['parent_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_tickets_parent_id', 'tickets', type_='foreignkey')
    op.drop_index('ix_tickets_repo_id_kind', table_name='tickets')
    op.drop_index('ix_tickets_parent_id', table_name='tickets')
    op.drop_column('tickets', 'parent_id')
    op.drop_column('tickets', 'kind')
