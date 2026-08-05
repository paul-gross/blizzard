"""graph node session_source — targeted node-entry resume (issue #115, hub store tree)

``session_source`` carries a ``resume:<node>``'s parsed target; nullable, no backfill.
Revision ID: 20260721_1008_hub_graph_node_session_source
Revises: 20260720_1000_hub_chunk_intended_migration
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1008_hub_graph_node_session_source"
down_revision: str | None = "20260721_1000_hub_escalation_decision_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMN = "session_source"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
