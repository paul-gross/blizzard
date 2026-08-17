"""graph artifacts — one row per graph mint's ``artifacts:`` declaration, keyed
``(graph_id, name)`` — the runner's own mirror of the hub's baked mint

Revision ID: 20260817_1000_runner_graph_artifacts
Revises: 20260816_1100_runner_transcript_agent_tool_use_ids
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import graph_artifacts

revision: str = "20260817_1000_runner_graph_artifacts"
down_revision: str | None = "20260816_1100_runner_transcript_agent_tool_use_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (graph_artifacts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
