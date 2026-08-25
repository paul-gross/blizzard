"""graph node proposes_work_items — the node-level policy legalizing proposed work items
on its completion (D4). Nullable, no backfill.

Revision ID: 20260825_1050_graph_node_proposes_work_items
Revises: 20260825_1000_chunk_deleted
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_1050_graph_node_proposes_work_items"
down_revision: str | None = "20260825_1000_chunk_deleted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMN = "proposes_work_items"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
