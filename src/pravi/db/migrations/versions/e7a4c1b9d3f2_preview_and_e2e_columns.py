"""preview deploy + e2e verdict columns (ADR 0007)

Adds the minimum persistence the deploy → e2e → repair loop needs:

  - repos.cf_pages_project  — which Cloudflare Pages project builds this
    repo. The create-repo flow already computed this and threw it away, so
    "where do I look for a preview deployment?" was unanswerable at ticket
    time.
  - repos.cf_custom_domain  — production custom domain, if attached.
  - tickets.preview_url     — the per-commit preview the suite ran against.
  - tickets.e2e_verdict     — the outcome, as a report field.

All nullable with no server default and no backfill: absent values mean
"this predates the feature", which is exactly how the workflow gates the
whole leg off.

Revision ID: e7a4c1b9d3f2
Revises: d8f1a2b3c5e4
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a4c1b9d3f2"
down_revision: Union[str, None] = "d8f1a2b3c5e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "repos", sa.Column("cf_pages_project", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "repos", sa.Column("cf_custom_domain", sa.String(length=255), nullable=True)
    )
    op.add_column("tickets", sa.Column("preview_url", sa.Text(), nullable=True))
    op.add_column(
        "tickets", sa.Column("e2e_verdict", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tickets", "e2e_verdict")
    op.drop_column("tickets", "preview_url")
    op.drop_column("repos", "cf_custom_domain")
    op.drop_column("repos", "cf_pages_project")
