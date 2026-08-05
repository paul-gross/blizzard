"""transition graph-provenance — adds ``transitions.graph_id``, backfilled config-free
from each transition's own chunk pin (hub store tree, issue #90)

Revision ID: 20260718_1215_hub_transition_graph_id
Revises: 20260718_1200_hub_route_token_minted
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_1215_hub_transition_graph_id"
down_revision: str | None = "20260718_1200_hub_route_token_minted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The chunk's graph pin — the backfill source. A local, revision-pinned literal.
_CHUNKS = sa.Table(
    "chunks",
    sa.MetaData(),
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, nullable=False),
)

# The pre-reshape transition shape — only the two columns the backfill reads.
_TRANSITIONS_ID = sa.Table(
    "transitions",
    sa.MetaData(),
    sa.Column("transition_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
)

# The reshaped transition shape — a second literal, so the UPDATE names ``graph_id``
# while the SELECT above runs before the column exists.
_TRANSITIONS_GRAPH = sa.Table(
    "transitions",
    sa.MetaData(),
    sa.Column("transition_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("graph_id", sa.String, nullable=True),
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("transitions")}
    if "graph_id" in columns:
        return  # already reshaped — guards the revision itself, not per-row

    chunk_graph = {r.chunk_id: r.graph_id for r in bind.execute(sa.select(_CHUNKS)).all()}
    rows = bind.execute(sa.select(_TRANSITIONS_ID)).all()

    with op.batch_alter_table("transitions") as batch:
        batch.add_column(sa.Column("graph_id", sa.String, nullable=True))

    for row in rows:
        bind.execute(
            _TRANSITIONS_GRAPH.update()
            .where(_TRANSITIONS_GRAPH.c.transition_id == row.transition_id)
            .values(graph_id=chunk_graph[row.chunk_id])
        )

    with op.batch_alter_table("transitions") as batch:
        batch.alter_column("graph_id", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("transitions")}
    if "graph_id" not in columns:
        return  # already the pre-reshape shape

    with op.batch_alter_table("transitions") as batch:
        batch.drop_column("graph_id")
