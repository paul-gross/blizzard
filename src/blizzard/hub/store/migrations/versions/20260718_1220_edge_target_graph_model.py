"""cross-graph edge per-choice model override — add ``graph_edges.to_graph_model`` (hub store tree, issue #90)

Phase 2 of the cross-graph-migration change: a judgement choice may declare a
cross-graph target (``to: graph:<name>``) and, optionally, a per-choice ``model:``
override applied when that choice migrates the chunk to the target graph. The target
graph itself needs no column — it rides in the existing ``to_node_name`` as the raw
``graph:<name>`` string and is re-derived on load (``graph.target_graph_of``). The
model override is the one authored value not encoded there, so it gets this column.

Additive, nullable — no backfill (a pre-#90 edge has no override, which is exactly
``NULL``). Guarded by an existence check so it is idempotent and no-ops on a fresh
store, where the walking-skeleton revision's frozen ``graph_edges`` literal is the one
that shipped without the column (see that revision's docstring — the same freeze the
``chunks``/``transitions`` reshapes use).

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
