"""agent_drafts table

Revision ID: e3a7f9c8b1d2
Revises: daf5d2562f80
Create Date: 2026-05-26 18:00:00.000000

Unified persistence for backgrounded architect drafts (decompose, plan).
Same pattern as `clarifications` — kicked off, polled, finalized with a
JSON payload whose shape varies by `kind`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3a7f9c8b1d2'
down_revision: Union[str, None] = '45ce5d528341'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_drafts',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('ticket_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('raw_md', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('prompt_version', sa.String(length=64), nullable=True),
        sa.Column('num_turns', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('total_cost_usd', sa.Float(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_agent_drafts_ticket_kind_id',
        'agent_drafts',
        ['ticket_id', 'kind', 'id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_agent_drafts_ticket_kind_id', table_name='agent_drafts')
    op.drop_table('agent_drafts')
