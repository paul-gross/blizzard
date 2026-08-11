"""transcript_segments.supersedes (blizzard#250) — a re-shipped segment's pointer at the one
it replaces, so a lease read drops the superseded segment instead of concatenating both.

Revision ID: 20260811_0905_hub_transcript_segment_supersedes
Revises: 20260809_1800_hub_transcript_segment_record_truncated
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0905_hub_transcript_segment_supersedes"
down_revision: str | None = "20260809_1800_hub_transcript_segment_record_truncated"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "supersedes" not in _columns(bind, "transcript_segments"):
        op.add_column("transcript_segments", sa.Column("supersedes", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "supersedes" in _columns(bind, "transcript_segments"):
        op.drop_column("transcript_segments", "supersedes")
