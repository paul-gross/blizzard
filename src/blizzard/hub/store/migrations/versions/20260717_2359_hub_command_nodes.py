"""generic hub command nodes: ``graph_nodes.run``, the JSON command list null on every other
node, and ``hub_exec_slot``, the fleet-wide serialization lease as a FACT (``bzh:facts-not-status``).

Revision ID: 20260717_2359_hub_command_nodes
Revises: 20260717_2345_hub_chunk_bounces
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260717_2359_hub_command_nodes"
down_revision: str | None = "20260717_2345_hub_chunk_bounces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_hub_exec_slot = sa.Table(
    "hub_exec_slot",
    _frozen_metadata,
    sa.Column("slot_id", sa.String, primary_key=True),
    sa.Column("holder_chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("acquired_at", UtcDateTime, nullable=False),
    sa.Column("released_at", UtcDateTime, nullable=True),
)

_TABLE = "graph_nodes"
_COLUMN = "run"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))
    _hub_exec_slot.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    _hub_exec_slot.drop(bind, checkfirst=True)
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
