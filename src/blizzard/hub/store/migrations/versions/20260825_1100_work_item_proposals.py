"""work_item_proposals — a node completion's proposed work items, riding its transition
or migration fact. One new table, FROZEN (``bzh:frozen-revisions``) since a later
revision adds ``runner_id`` (blizzard#366).

Revision ID: 20260825_1100_work_item_proposals
Revises: 20260825_1050_graph_node_proposes_work_items
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260825_1100_work_item_proposals"
down_revision: str | None = "20260825_1050_graph_node_proposes_work_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no `runner_id` — reshaped by 20260825_1150.
# The `chunks` entry below is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table("chunks", _frozen_metadata, sa.Column("chunk_id", sa.String, primary_key=True))
_work_item_proposals = sa.Table(
    "work_item_proposals",
    _frozen_metadata,
    sa.Column("proposal_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("node_name", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("data", sa.Text, nullable=False),
    sa.Column("proposed_at", UtcDateTime, nullable=False),
)

_TABLES = (_work_item_proposals,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
