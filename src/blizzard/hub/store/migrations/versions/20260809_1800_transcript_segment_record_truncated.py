"""transcript_segments.record_truncated (blizzard#246, hub store tree) — the runner's
own cap declaration, distinct from `rejected`. Nullable, no backfill.

Revision ID: 20260809_1800_hub_transcript_segment_record_truncated
Revises: 20260809_1200_hub_transcript_segments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_1800_hub_transcript_segment_record_truncated"
down_revision: str | None = "20260809_1200_hub_transcript_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "record_truncated"


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind, "transcript_segments"):
        op.add_column("transcript_segments", sa.Column(_COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind, "transcript_segments"):
        op.drop_column("transcript_segments", _COLUMN)
