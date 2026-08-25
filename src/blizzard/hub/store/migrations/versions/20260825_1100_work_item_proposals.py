"""work_item_proposals — a node completion's proposed work items, riding its transition
or migration fact. One new table.

Revision ID: 20260825_1100_work_item_proposals
Revises: 20260825_1050_graph_node_proposes_work_items
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import work_item_proposals

revision: str = "20260825_1100_work_item_proposals"
down_revision: str | None = "20260825_1050_graph_node_proposes_work_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (work_item_proposals,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
