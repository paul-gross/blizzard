"""``chunk_restarts.from_graph_id`` — the graph the move departed, so a cross-graph restart's
two ends resolve independently as a migration's do; additive and nullable, no backfill (#371)

Revision ID: 20260819_2200_chunk_restart_from_graph
Revises: 20260819_1100_work_items
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_2200_chunk_restart_from_graph"
down_revision: str | None = "20260819_1100_work_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunk_restarts"
_COLUMN = "from_graph_id"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
