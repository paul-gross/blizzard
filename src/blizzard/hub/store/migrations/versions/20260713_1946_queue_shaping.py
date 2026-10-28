"""queue shaping — creates ``queue_positions`` (the append-only ready-queue ordering
fact) and ``chunk_grouped`` (a merged-away chunk naming its survivor)

Revision ID: 20260713_1946_hub_queue_shaping
Revises: 20260713_1803_hub_gate_decisions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260713_1946_hub_queue_shaping"
down_revision: str | None = "20260713_1803_hub_gate_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_queue_positions = sa.Table(
    "queue_positions",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("position", sa.Float(), nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
)
sa.Index("ix_queue_positions_chunk_id", _queue_positions.c.chunk_id)
_chunk_grouped = sa.Table(
    "chunk_grouped",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("grouped_into", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("grouped_at", UtcDateTime, nullable=False),
)
sa.Index("ix_chunk_grouped_grouped_at_id", _chunk_grouped.c.grouped_at, _chunk_grouped.c.id)

_TABLES = [_queue_positions, _chunk_grouped]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
