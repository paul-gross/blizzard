"""chunk_deleted — the fact that makes an unacquired chunk ephemeral by deletion.
One new table, ``checkfirst`` so a fresh ``base -> head`` and an upgrade converge.

Revision ID: 20260825_1000_chunk_deleted
Revises: 20260819_2200_chunk_restart_from_graph
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260825_1000_chunk_deleted"
down_revision: str | None = "20260819_2200_chunk_restart_from_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``chunk_deleted`` is a local literal of the hub store's declared shape.
# ``chunks`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_chunk_deleted = sa.Table(
    "chunk_deleted",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("deleted_at", UtcDateTime, nullable=False),
    sa.Column("deleted_by", sa.String, nullable=False),
)
sa.Index("ix_chunk_deleted_deleted_at_id", _chunk_deleted.c.deleted_at, _chunk_deleted.c.id)

_TABLES = (_chunk_deleted,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
