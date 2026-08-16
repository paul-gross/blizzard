"""transcript_segments.agent_tool_use_ids (blizzard#338) — the agent-id -> spawning
``tool_use_id`` map, so a sidecar read AFTER the result that named the pair still links.

Revision ID: 20260816_1100_runner_transcript_agent_tool_use_ids
Revises: 20260811_1400_runner_escalation_closures
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_1100_runner_transcript_agent_tool_use_ids"
down_revision: str | None = "20260811_1400_runner_escalation_closures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "agent_tool_use_ids" not in _columns(bind, "transcript_segments"):
        op.add_column("transcript_segments", sa.Column("agent_tool_use_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "agent_tool_use_ids" in _columns(bind, "transcript_segments"):
        op.drop_column("transcript_segments", "agent_tool_use_ids")
