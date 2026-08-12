"""transcript events (blizzard#254, epic:transcripts, hub store tree) — the derived,
re-derivable event stream and its per-segment derivation marker. Creates both, ``checkfirst``.

Revision ID: 20260812_1200_hub_transcript_events
Revises: 20260811_0905_hub_transcript_segment_supersedes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260812_1200_hub_transcript_events"
down_revision: str | None = "20260811_0905_hub_transcript_segment_supersedes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape (``bzh:frozen-revisions``). The ``chunks`` entry below
# is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_transcript_events = sa.Table(
    "transcript_events",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("segment_id", sa.String, nullable=False),
    sa.Column("extractor_version", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("turn_path", sa.String, nullable=False),
    sa.Column("occurrence", sa.Integer, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("spawn_generation", sa.Integer, nullable=False),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("depth", sa.Integer, nullable=False),
    sa.Column("agent_type", sa.String, nullable=True),
    sa.Column("occurred_at", UtcDateTime, nullable=True),
    sa.UniqueConstraint(
        "segment_id", "extractor_version", "kind", "turn_path", "occurrence", name="uq_transcript_events_natural_key"
    ),
)
_transcript_event_derivations = sa.Table(
    "transcript_event_derivations",
    _frozen_metadata,
    sa.Column("segment_id", sa.String, primary_key=True),
    sa.Column("extractor_version", sa.String, primary_key=True),
    sa.Column("content_fingerprint", sa.String, nullable=False),
    sa.Column("derived_at", UtcDateTime, nullable=False),
    sa.Column("event_count", sa.Integer, nullable=False),
    sa.Column("complete", sa.Boolean, nullable=False),
)

_TABLES = (_transcript_events, _transcript_event_derivations)
_CHUNK_INDEX = sa.Index("ix_transcript_events_chunk_id", _transcript_events.c.chunk_id)
_SEGMENT_INDEX = sa.Index("ix_transcript_events_segment_id", _transcript_events.c.segment_id)
_INDEXES = (_CHUNK_INDEX, _SEGMENT_INDEX)


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
