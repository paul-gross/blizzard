"""graph artifacts — one row per graph-level ``artifacts:`` declaration, keyed
``(graph_id, name)`` and baked immutable with the graph that owns it

Revision ID: 20260817_0900_hub_graph_artifacts
Revises: 20260812_1300_hub_transcript_events_subject_tool
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0900_hub_graph_artifacts"
down_revision: str | None = "20260812_1300_hub_transcript_events_subject_tool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``graph_artifacts`` is a local literal of the hub store's declared shape.
# ``graphs`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "graphs",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, primary_key=True),
)
_graph_artifacts = sa.Table(
    "graph_artifacts",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, sa.ForeignKey("graphs.graph_id"), primary_key=True),
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
)

_TABLES = (_graph_artifacts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
