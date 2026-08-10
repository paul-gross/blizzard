"""transcript_segments.truncated_reasons_warned + truncated_reason_severity (blizzard#246,
F2) — latches the truncation warning per (segment, reason), display precedence explicit.

Revision ID: 20260810_1200_runner_transcript_truncated_reasons_warned
Revises: 20260809_2330_runner_transcript_sidechain_warned_agents
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_1200_runner_transcript_truncated_reasons_warned"
down_revision: str | None = "20260809_2330_runner_transcript_sidechain_warned_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("truncated_reason_severity", "truncated_reasons_warned")


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "transcript_segments")
    if "truncated_reason_severity" not in existing:
        op.add_column("transcript_segments", sa.Column("truncated_reason_severity", sa.Integer(), nullable=True))
    if "truncated_reasons_warned" not in existing:
        op.add_column("transcript_segments", sa.Column("truncated_reasons_warned", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "transcript_segments")
    for column in _COLUMNS:
        if column in existing:
            op.drop_column("transcript_segments", column)
