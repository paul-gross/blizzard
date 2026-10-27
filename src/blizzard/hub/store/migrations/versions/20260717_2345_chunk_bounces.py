"""delivery kick-back bounces + per-node bounce cap (hub store tree). Adds
``chunk_bounces`` (natural-keyed on ``(chunk_id, epoch)``) and ``graph_nodes.bounce_cap``.

Revision ID: 20260717_2345_hub_chunk_bounces
Revises: 20260718_0930_hub_runner_local_pause_reason
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260717_2345_hub_chunk_bounces"
down_revision: str | None = "20260718_0930_hub_runner_local_pause_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_chunk_bounces = sa.Table(
    "chunk_bounces",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("cause", sa.String, nullable=False),
    sa.Column("envelope", sa.Text, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)
sa.Index("ix_chunk_bounces_chunk_id_epoch", _chunk_bounces.c.chunk_id, _chunk_bounces.c.epoch)

_TABLE = "graph_nodes"
_COLUMN = "bounce_cap"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    _chunk_bounces.create(bind, checkfirst=True)
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
    _chunk_bounces.drop(bind, checkfirst=True)
