"""chunk_completed — an operator's manual chunk completion (hub store tree).
One new table, ``checkfirst`` so a fresh ``base -> head`` and an upgrade converge.

Revision ID: 20260818_2100_chunk_completed
Revises: 20260818_0900_hub_graph_sessions_compaction_window
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260818_2100_chunk_completed"
down_revision: str | None = "20260818_0900_hub_graph_sessions_compaction_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_chunk_completed = sa.Table(
    "chunk_completed",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("completed_at", UtcDateTime, nullable=False),
    sa.Column("completed_by", sa.String, nullable=False),
)
sa.Index("ix_chunk_completed_chunk_id", _chunk_completed.c.chunk_id)
sa.Index("ix_chunk_completed_completed_at_id", _chunk_completed.c.completed_at, _chunk_completed.c.id)

_TABLES = (_chunk_completed,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
