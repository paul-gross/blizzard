"""cross-graph edge per-choice model override — add ``graph_edges.to_graph_model`` (hub store tree, issue #90)

The target graph rides in ``to_node_name``; only the ``model:`` override needs a column.
Revision ID: 20260718_1220_hub_edge_target_graph_model
Revises: 20260718_1215_hub_transition_graph_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_1220_hub_edge_target_graph_model"
down_revision: str | None = "20260718_1215_hub_transition_graph_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("graph_edges")}
    if "to_graph_model" in columns:
        return  # already present (a fresh store's walking-skeleton create, or a re-run)
    with op.batch_alter_table("graph_edges") as batch:
        batch.add_column(sa.Column("to_graph_model", sa.String, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("graph_edges")}
    if "to_graph_model" not in columns:
        return
    with op.batch_alter_table("graph_edges") as batch:
        batch.drop_column("to_graph_model")
