"""graph artifacts — one row per graph-level ``artifacts:`` declaration, keyed
``(graph_id, name)`` and baked immutable with the graph that owns it

Revision ID: 20260817_0900_hub_graph_artifacts
Revises: 20260812_1300_hub_transcript_events_subject_tool
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import graph_artifacts

revision: str = "20260817_0900_hub_graph_artifacts"
down_revision: str | None = "20260812_1300_hub_transcript_events_subject_tool"
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
