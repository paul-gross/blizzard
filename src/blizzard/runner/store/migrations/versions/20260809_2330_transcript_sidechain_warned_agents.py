"""transcript_segments.sidechain_warned_agents (blizzard#246) — latches the
dropped-sidechain fact-lane warning per (segment, agent_id) instead of firing it every tick.

Revision ID: 20260809_2330_runner_transcript_sidechain_warned_agents
Revises: 20260808_2300_runner_transcript_segments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_2330_runner_transcript_sidechain_warned_agents"
down_revision: str | None = "20260808_2300_runner_transcript_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "sidechain_warned_agents"


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind, "transcript_segments"):
        op.add_column("transcript_segments", sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind, "transcript_segments"):
        op.drop_column("transcript_segments", _COLUMN)
