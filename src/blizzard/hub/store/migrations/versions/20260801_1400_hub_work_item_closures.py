"""work_item_closures — delivery closure attempt outcomes (hub store tree).
One new table, ``checkfirst`` so a fresh ``base -> head`` and an upgrade converge.

Revision ID: 20260801_1400_hub_work_item_closures
Revises: 20260731_1200_hub_transitions_recorded_at
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260801_1400_hub_work_item_closures"
down_revision: str | None = "20260731_1200_hub_transitions_recorded_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_work_item_closures = sa.Table(
    "work_item_closures",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
    sa.Column("outcome", sa.String, nullable=False),
    sa.Column("reason", sa.String, nullable=True),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("chunk_id", "source", "ref", "outcome", name="uq_work_item_closures_chunk_source_ref_outcome"),
)

_TABLES = (_work_item_closures,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
