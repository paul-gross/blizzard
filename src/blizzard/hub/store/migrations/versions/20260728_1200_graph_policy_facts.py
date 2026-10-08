"""graph policy facts — the per-graph follow-latest tri-state (hub store tree)

Append-only, newest-fact-wins, ``checkfirst``; ``follow_latest`` is nullable, and NULL means inherit.
Revision ID: 20260728_1200_hub_graph_policy_facts
Revises: 20260726_1200_hub_chunk_work_refs_rename
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260728_1200_hub_graph_policy_facts"
down_revision: str | None = "20260726_1200_hub_chunk_work_refs_rename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``graph_policy_facts`` is a local literal of the hub store's declared
# shape. ``graphs`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "graphs",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, primary_key=True),
)
_graph_policy_facts = sa.Table(
    "graph_policy_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("graph_id", sa.String, sa.ForeignKey("graphs.graph_id"), nullable=False),
    sa.Column("follow_latest", sa.Boolean, nullable=True),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)

_TABLES = (_graph_policy_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
