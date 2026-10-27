"""usage facts — harness cost/token telemetry per invocation, hub-side (hub store tree)

One append-only row per invocation, **not** epoch-fenced: stale spend is real spend.
Revision ID: 20260717_2330_hub_usage_facts
Revises: 20260717_2318_hub_chunk_model_selection
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260717_2330_hub_usage_facts"
down_revision: str | None = "20260717_2318_hub_chunk_model_selection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_usage_facts = sa.Table(
    "usage_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("model", sa.String, nullable=False),
    sa.Column("harness_id", sa.String, nullable=True),
    sa.Column("harness_version", sa.String, nullable=True),
    sa.Column("input_tokens", sa.Integer, nullable=False),
    sa.Column("output_tokens", sa.Integer, nullable=False),
    sa.Column("cache_read_tokens", sa.Integer, nullable=False),
    sa.Column("cache_create_tokens", sa.Integer, nullable=False),
    sa.Column("cost_usd", sa.Float, nullable=True),
    sa.Column("estimated_cost_usd", sa.Float, nullable=True),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)
sa.Index("ix_usage_facts_chunk_id", _usage_facts.c.chunk_id)
sa.Index("ix_usage_facts_node_id", _usage_facts.c.node_id)
sa.Index("ix_usage_facts_recorded_at_id", _usage_facts.c.recorded_at, _usage_facts.c.id)

_TABLES = (_usage_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
