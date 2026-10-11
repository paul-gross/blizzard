"""work_item_runs (a run's identity).

Revision ID: 20260830_1835_work_item_runs
Revises: 20260829_1930_fact_tables_chunk_id_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_1835_work_item_runs"
down_revision: str | None = "20260829_1930_fact_tables_chunk_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``work_item_runs`` is a local literal of the hub store's declared shape.
# ``scopes`` and ``work_items`` are FK-resolution stubs: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "scopes",
    _frozen_metadata,
    sa.Column("slug", sa.String, primary_key=True),
)
sa.Table(
    "work_items",
    _frozen_metadata,
    sa.Column("work_item_id", sa.String, primary_key=True),
)
_work_item_runs = sa.Table(
    "work_item_runs",
    _frozen_metadata,
    sa.Column("work_item_id", sa.String, sa.ForeignKey("work_items.work_item_id"), primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, sa.ForeignKey("scopes.slug"), nullable=False),
    sa.Column("mode", sa.String, nullable=False),
)


def upgrade() -> None:
    bind = op.get_bind()
    _work_item_runs.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    _work_item_runs.drop(bind, checkfirst=True)
