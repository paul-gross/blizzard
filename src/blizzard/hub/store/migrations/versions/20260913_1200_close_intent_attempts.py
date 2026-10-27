"""close_intent_attempts — one row per close-intent drain attempt, appended with its
outcome. One new table, no backfill: an empty table starts every pending intent immediately due.

Revision ID: 20260913_1200_close_intent_attempts
Revises: 20260913_1000_transcript_segments_content_digest
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260913_1200_close_intent_attempts"
down_revision: str | None = "20260913_1000_transcript_segments_content_digest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``close_intents`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "close_intents",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True),
)
_close_intent_attempts = sa.Table(
    "close_intent_attempts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("intent_id", sa.Integer, sa.ForeignKey("close_intents.id"), nullable=False),
    sa.Column("attempted_at", UtcDateTime, nullable=False),
    sa.Column("outcome", sa.String, nullable=False),
)
sa.Index("ix_close_intent_attempts_intent_id", _close_intent_attempts.c.intent_id)

_TABLES = (_close_intent_attempts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
