"""transcript_segments.supersedes (blizzard#250) — the re-ship's pointer at the segment it
replaces, so the hub's lease read can drop the superseded one instead of concatenating both.

Revision ID: 20260811_0900_runner_transcript_segment_supersedes
Revises: 20260810_1200_runner_transcript_truncated_reasons_warned
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0900_runner_transcript_segment_supersedes"
down_revision: str | None = "20260810_1200_runner_transcript_truncated_reasons_warned"
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
