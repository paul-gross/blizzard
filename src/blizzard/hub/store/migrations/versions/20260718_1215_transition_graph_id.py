"""transition graph-provenance — add ``transitions.graph_id`` + backfill (hub store tree, issue #90)

Phase 1 of the cross-graph-migration change: every transition gains the identity of
the graph it happened in, so a chunk's history survives a later cross-graph migration
(which re-pins ``chunks.graph_id``). Without it, hydration resolves every transition's
node ids against the chunk's *one* current pin — correct only while a chunk has run a
single graph. This is schema + backfill plumbing; no behaviour changes yet (every
existing transition remains same-graph, so its ``graph_id`` is exactly the chunk's
current pin).

``transitions`` is reshaped in place — SQLite has no ``ALTER COLUMN``, so the
nullable→backfill→NOT-NULL step uses ``op.batch_alter_table`` (the portable Alembic
idiom, ``bzh:sql-portable``).

**Deliberate deviation — local ``sa.Table`` literals, not ``import schema``:** like
``20260716_1512_pm_pointer_source_ref`` (see its docstring), a revision that *reshapes*
a table is a data migration pinned to a moment in time; importing head-of-tree
``schema.py`` would silently change what this revision does on a future checkout.

**Backfill rule (config-free, deterministic — rehearsable):** each transition's
``graph_id`` is its chunk's current ``chunks.graph_id``. Reading no configuration, the
same store's bytes migrate identically at any time, and every transition references a
real chunk (the ``chunk_id`` foreign key), so every row resolves.

**``downgrade()`` simply drops the column** — the provenance it added is fully
recoverable on re-upgrade from ``chunks.graph_id`` (every pre-migration transition is
same-graph), so down-then-up is stable: a re-upgrade re-derives the identical
``graph_id`` for every existing (same-graph) transition.

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

# The reshaped transition shape — the same two columns plus the added ``graph_id`` the
# backfill writes. A second literal (like pm-pointer's ``_OLD``/``_NEW`` pair) so the
# UPDATE names ``graph_id`` while the SELECT above runs before the column exists.
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
