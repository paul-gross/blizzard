"""Adds `ix_queue_positions_chunk_id` — the live-set queue reads look positions up by
the candidate chunk ids, not by scanning the append-only table.

Revision ID: 20260928_1000_queue_positions_chunk_id_index
Revises: 20260926_1000_hub_runner_declared_subscriptions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_1000_queue_positions_chunk_id_index"
down_revision: str | None = "20260926_1000_hub_runner_declared_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_queue_positions_chunk_id"


def _has_index() -> bool:
    return any(ix["name"] == _INDEX_NAME for ix in sa.inspect(op.get_bind()).get_indexes("queue_positions"))


def upgrade() -> None:
    # The queue-shaping revision creates its table from the live schema, which already
    # carries this index on a fresh store, so create it only where it is still missing.
    if not _has_index():
        op.create_index(_INDEX_NAME, "queue_positions", ["chunk_id"])


def downgrade() -> None:
    if _has_index():
        op.drop_index(_INDEX_NAME, table_name="queue_positions")
