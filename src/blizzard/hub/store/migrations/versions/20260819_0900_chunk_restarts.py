"""chunk_restarts — an operator's forced move of a chunk onto a node (#370, hub store tree). One
table, FROZEN (``bzh:frozen-revisions``) since a later revision adds ``from_graph_id`` (#371).

Revision ID: 20260819_0900_chunk_restarts
Revises: 20260818_2100_chunk_completed
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260819_0900_chunk_restarts"
down_revision: str | None = "20260818_2100_chunk_completed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_metadata = sa.MetaData()

# The FK target, declared only so ``chunk_restarts.chunk_id``'s ForeignKey resolves inside
# this revision's own MetaData; never created or dropped here.
sa.Table("chunks", _metadata, sa.Column("chunk_id", sa.String, primary_key=True))

chunk_restarts = sa.Table(
    "chunk_restarts",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("from_node_id", sa.String, nullable=True),
    sa.Column("to_node_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("decision_id", sa.String, nullable=True),
    sa.Column("restarted_by", sa.String, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)

_TABLES = (chunk_restarts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
