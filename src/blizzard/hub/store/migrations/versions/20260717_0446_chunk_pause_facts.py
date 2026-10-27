"""chunk pause facts — an operator-level brake over one chunk; append-only,
newest-fact-wins, mirroring ``runner_pause_facts`` (hub store tree)

Revision ID: 20260717_0446_hub_chunk_pause_facts
Revises: 20260716_2207_hub_route_seq_tiebreak
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260717_0446_hub_chunk_pause_facts"
down_revision: str | None = "20260716_2207_hub_route_seq_tiebreak"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_chunk_pause_facts = sa.Table(
    "chunk_pause_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("paused", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)
sa.Index("ix_chunk_pause_facts_chunk_id", _chunk_pause_facts.c.chunk_id)
sa.Index("ix_chunk_pause_facts_set_at_id", _chunk_pause_facts.c.set_at, _chunk_pause_facts.c.id)

_TABLES = (_chunk_pause_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
