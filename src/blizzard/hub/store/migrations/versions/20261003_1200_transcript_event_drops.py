"""The transcript segment drop fact: one append-only row per drop of a segment's derived events.

Revision ID: 20261003_1200_event_drops
Revises: 20261003_0900_egress_cursor
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261003_1200_event_drops"
down_revision: str | None = "20261003_0900_egress_cursor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transcript_event_drops"
_INDEX = "ix_transcript_event_drops_dropped_at_segment_id"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("segment_id", sa.String, nullable=False),
            sa.Column("chunk_id", sa.String, nullable=False),
            sa.Column("epoch", sa.Integer, nullable=False),
            sa.Column("spawn_generation", sa.Integer, nullable=False),
            sa.Column("dropped_at", UtcDateTime, nullable=False),
        )
    if _INDEX not in {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["dropped_at", "segment_id"])


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(_TABLE)
