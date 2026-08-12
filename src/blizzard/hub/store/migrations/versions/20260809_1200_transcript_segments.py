"""transcript segments (blizzard#247, epic:transcripts, hub store tree) — the append-only
per-record transcript table and its lane's own high-water table. Creates both, ``checkfirst``.

Revision ID: 20260809_1200_hub_transcript_segments
Revises: 20260803_1000_hub_escalation_wrapped_takeover
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import transcript_high_water

revision: str = "20260809_1200_hub_transcript_segments"
down_revision: str | None = "20260803_1000_hub_escalation_wrapped_takeover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no ``record_truncated``/``supersedes`` columns, both
# added by later revisions (``bzh:frozen-revisions``). The ``chunks`` entry below is an
# FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_transcript_segments = sa.Table(
    "transcript_segments",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("segment_id", sa.String, nullable=False),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("spawn_generation", sa.Integer, nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("turn_range_start", sa.Integer, nullable=False),
    sa.Column("turn_range_end", sa.Integer, nullable=False),
    sa.Column("final", sa.Boolean, nullable=False),
    sa.Column("rejected", sa.Boolean, nullable=False),
    sa.Column("rejection_reason", sa.String, nullable=True),
    sa.Column("byte_count", sa.Integer, nullable=False),
    sa.Column("codec", sa.String, nullable=True),
    sa.Column("content", sa.LargeBinary, nullable=True),
    sa.Column("normalizer_version", sa.String, nullable=False),
    sa.Column("harness_version", sa.String, nullable=True),
    sa.Column("received_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("segment_id", "turn_range_start", name="uq_transcript_segments_segment_turn_start"),
)

_TABLES = (_transcript_segments, transcript_high_water)
_CHUNK_INDEX = sa.Index("ix_transcript_segments_chunk_id", _transcript_segments.c.chunk_id)
_RUNNER_RECEIVED_INDEX = sa.Index(
    "ix_transcript_segments_runner_received_at",
    _transcript_segments.c.runner_id,
    _transcript_segments.c.received_at,
)
_SEGMENT_INDEX = sa.Index("ix_transcript_segments_segment_id", _transcript_segments.c.segment_id)
_INDEXES = (_CHUNK_INDEX, _RUNNER_RECEIVED_INDEX, _SEGMENT_INDEX)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)
    for index in _INDEXES:
        index.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for index in reversed(_INDEXES):
        index.drop(bind, checkfirst=True)
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
