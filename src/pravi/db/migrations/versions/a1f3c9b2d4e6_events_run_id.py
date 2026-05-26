"""events.run_id + indexes for live telemetry

Adds a nullable run_id FK on events so we can tie each agent event back to
the Run that produced it, and indexes for the two hot replay queries:
  - (ticket_id, id) — "give me events for this ticket since id X"
  - (run_id, id)    — "render the timeline of this specific run"

Revision ID: a1f3c9b2d4e6
Revises: 38b585b5fd5a
Create Date: 2026-05-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1f3c9b2d4e6"
down_revision: Union[str, None] = "38b585b5fd5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("run_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_events_run_id_runs",
        "events",
        "runs",
        ["run_id"],
        ["id"],
    )
    op.create_index("ix_events_ticket_id_id", "events", ["ticket_id", "id"])
    op.create_index("ix_events_run_id_id", "events", ["run_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_events_run_id_id", table_name="events")
    op.drop_index("ix_events_ticket_id_id", table_name="events")
    op.drop_constraint("fk_events_run_id_runs", "events", type_="foreignkey")
    op.drop_column("events", "run_id")
