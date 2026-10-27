"""graph lifecycle facts — a reversible retire/re-enable brake over one graph_id

Append-only, newest-fact-wins, created ``checkfirst``; the ``graphs`` table itself stays immutable.
Revision ID: 20260719_0900_hub_graph_lifecycle_facts
Revises: 20260718_1300_hub_runner_env_capacity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260719_0900_hub_graph_lifecycle_facts"
down_revision: str | None = "20260718_1300_hub_runner_env_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``graphs`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "graphs",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, primary_key=True),
)
_graph_lifecycle_facts = sa.Table(
    "graph_lifecycle_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("graph_id", sa.String, sa.ForeignKey("graphs.graph_id"), nullable=False),
    sa.Column("retired", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)
sa.Index("ix_graph_lifecycle_facts_graph_id", _graph_lifecycle_facts.c.graph_id)

_TABLES = (_graph_lifecycle_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
