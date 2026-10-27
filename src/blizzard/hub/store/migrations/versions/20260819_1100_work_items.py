"""work_items — hub-owned work items and their per-source ref sequence (hub store
tree). Two new tables, ``checkfirst`` so a fresh ``base -> head`` and an
upgrade converge.

Revision ID: 20260819_1100_work_items
Revises: 20260819_0900_chunk_restarts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260819_1100_work_items"
down_revision: str | None = "20260819_0900_chunk_restarts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``).
_frozen_metadata = sa.MetaData()
_work_items = sa.Table(
    "work_items",
    _frozen_metadata,
    sa.Column("work_item_id", sa.String, primary_key=True),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("author_kind", sa.String, nullable=False),
    sa.Column("author_payload", sa.Text, nullable=False),
    sa.Column("stated_priority", sa.String, nullable=True),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("edited_at", UtcDateTime, nullable=False),
    sa.Column("closed_at", UtcDateTime, nullable=True),
    sa.Column("closure", sa.String, nullable=True),
    sa.Column("routine_name", sa.String, nullable=True),
    sa.Column("scope_slug", sa.String, nullable=True),
    sa.Column("run_mode", sa.String, nullable=True),
    sa.UniqueConstraint("source", "ref", name="uq_work_items_source_ref"),
)
sa.Index("ix_work_items_source", _work_items.c.source)
_work_item_sequence = sa.Table(
    "work_item_sequence",
    _frozen_metadata,
    sa.Column("source", sa.String, primary_key=True),
    sa.Column("next_ref", sa.Integer, nullable=False),
)

_TABLES = (_work_items, _work_item_sequence)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
