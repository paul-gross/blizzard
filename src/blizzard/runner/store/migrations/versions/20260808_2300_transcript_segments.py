"""transcript segments and their own outbound buffer — the dedicated transcript lane
(runner store tree, issue #246)

Revision ID: 20260808_2300_runner_transcript_segments
Revises: 20260801_1500_runner_external_usage_samples
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260808_2300_runner_transcript_segments"
down_revision: str | None = "20260801_1500_runner_external_usage_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision's own original shape (`bzh:frozen-revisions`) — NOT imported from
# live `schema.py`, which later revisions in this tree (0900, 1400) go on to reshape.
_frozen_metadata = sa.MetaData()

transcript_segments = sa.Table(
    "transcript_segments",
    _frozen_metadata,
    sa.Column("segment_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("generation", sa.Integer, nullable=False),
    sa.Column("lease_id", sa.String, nullable=False),
    sa.Column("session_id", sa.String, nullable=False),
    sa.Column("cursor", sa.String, nullable=True),
    sa.Column("shipped_bytes", sa.Integer, nullable=False),
    sa.Column("shipped_turns", sa.Integer, nullable=False),
    sa.Column("truncated_reason", sa.String, nullable=True),
    sa.Column("finalized_at", UtcDateTime, nullable=True),
    sa.Column("stamped_at", UtcDateTime, nullable=False),
)

transcript_outbound_buffer = sa.Table(
    "transcript_outbound_buffer",
    _frozen_metadata,
    sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("segment_id", sa.String, nullable=False),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("acked_at", UtcDateTime, nullable=True),
)

_TABLES = (transcript_segments, transcript_outbound_buffer)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
