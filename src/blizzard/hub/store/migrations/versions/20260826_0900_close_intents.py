"""close_intents — the durable close-intent outbox. One new table.

Revision ID: 20260826_0900_close_intents
Revises: 20260825_1300_work_item_strikes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260826_0900_close_intents"
down_revision: str | None = "20260825_1300_work_item_strikes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``close_intents`` is a local literal of the hub store's declared shape.
# ``chunks`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_close_intents = sa.Table(
    "close_intents",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
    sa.Column("enqueued_at", UtcDateTime, nullable=False),
    sa.Column("retired_at", UtcDateTime, nullable=True),
    sa.UniqueConstraint("chunk_id", "source", "ref", name="uq_close_intents_chunk_source_ref"),
)
sa.Index("ix_close_intents_pending", _close_intents.c.retired_at)

_TABLES = (_close_intents,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
