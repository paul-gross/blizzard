"""route capability token — creates the append-only ``route_token_minted`` fact table:
one unguessable per-acquisition secret per claim (hub store tree)

Revision ID: 20260718_1200_hub_route_token_minted
Revises: 20260718_1130_hub_runner_token
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260718_1200_hub_route_token_minted"
down_revision: str | None = "20260718_1130_hub_runner_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``route_token_minted`` is a local literal of the hub store's declared
# shape. ``chunks`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_route_token_minted = sa.Table(
    "route_token_minted",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("token_hash", sa.Text, nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("minted_at", UtcDateTime, nullable=False),
)
sa.Index("ix_route_token_minted_chunk_id", _route_token_minted.c.chunk_id)


def upgrade() -> None:
    bind = op.get_bind()
    _route_token_minted.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    _route_token_minted.drop(bind, checkfirst=True)
