"""cross-graph migration fact table — chunk_migrations (hub store tree, issue #90). A
frozen local literal, not a ``schema.py`` import (``bzh:frozen-revisions``).

Revision ID: 20260718_1225_hub_chunk_migrations
Revises: 20260718_1220_hub_edge_target_graph_model
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260718_1225_hub_chunk_migrations"
down_revision: str | None = "20260718_1220_hub_edge_target_graph_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no ``source`` column, added by a later revision. The
# ``chunks`` entry below is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
chunk_migrations = sa.Table(
    "chunk_migrations",
    _frozen_metadata,
    sa.Column("migration_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("from_node_id", sa.String, nullable=True),
    sa.Column("from_graph_id", sa.String, nullable=False),
    sa.Column("to_graph_id", sa.String, nullable=False),
    sa.Column("landed_node_id", sa.String, nullable=True),
    sa.Column("choice_name", sa.String, nullable=True),
    sa.Column("decision_id", sa.String, nullable=True),
    sa.Column("model_after", sa.String, nullable=True),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)


def upgrade() -> None:
    bind = op.get_bind()
    chunk_migrations.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    chunk_migrations.drop(bind, checkfirst=True)
