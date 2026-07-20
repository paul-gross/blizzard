"""walking-skeleton fact tables (hub store tree)

The hub store's first real schema (P6): the fact tables the ingest -> claim ->
commit -> deliver -> land loop derives every chunk status from
(``bzh:facts-not-status``). The tables are defined once in
``blizzard.hub.store.schema`` (the metadata Alembic targets for autogenerate);
this revision creates exactly this revision's subset in FK-dependency order, so a
later revision that adds tables to the same metadata does not get re-created here.

``chunk_pm_pointers``, ``route_created``, ``route_released``, ``chunks``,
``transitions``, ``graph_edges``, and ``chunk_stopped`` are the exceptions (as of
``0013_pm_pointer_source_ref``, ``0015_route_seq_tiebreak``,
``0018_chunk_model_selection``, ``transition_graph_id``,
``edge_target_graph_model`` — the last two issue #90 — and
``20260719_2000_hub_chunk_stopped_by`` respectively): importing them
from ``schema.py`` here would mean this revision's *historical* shape silently follows
whatever ``schema.py`` says today — exactly the bug 0013's own docstring names and
refuses to repeat. This revision instead declares its own frozen literal for each —
``{provider, url}`` for pointers, no ``seq`` column for the two route tables, no
``model`` column for ``chunks``, no ``graph_id`` column for ``transitions``, no
``to_graph_model`` column for ``graph_edges``, no ``stopped_by`` column for
``chunk_stopped`` — so upgrading from ``base`` always recreates the column shape this
revision actually shipped with; 0013, 0014, 0018, the two graph-provenance/migration
revisions, and the chunk-stopped-by revision are what reshape them from there. The
frozen literals still declare their ``chunk_id``/``graph_id`` foreign keys (via
same-MetaData resolution stubs, not live imports — see below) so a fresh store's
schema matches ``schema.py``'s declared FKs (``bzh:sql-portable``: postgres is the
same schema under a different URL).

Revision ID: 20260713_1218_hub_walking_skeleton
Revises: 20260713_1112_hub_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import (
    artifacts,
    delivery_landed,
    delivery_repo_landed,
    escalations,
    graph_choices,
    graph_nodes,
    graphs,
    lease_facts,
    route_environments,
)

revision: str = "20260713_1218_hub_walking_skeleton"
down_revision: str | None = "20260713_1112_hub_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no `model` column — reshaped by
# 0018_chunk_model_selection. Not imported from schema.py (see the module docstring).
# `chunks.graph_id` FKs to `graphs.graph_id`; `graphs` itself is unreshaped and created
# below from the real schema.py import, but it lives in a *different* MetaData than this
# frozen one, so a bare `sa.ForeignKey("graphs.graph_id")` needs its own same-MetaData
# resolution stub — never added to `_TABLES`, never created or dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "graphs",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, primary_key=True),
)
_chunks = sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, sa.ForeignKey("graphs.graph_id"), nullable=False),
    sa.Column("minted_at", UtcDateTime, nullable=False),
)
_chunk_pm_pointers = sa.Table(
    "chunk_pm_pointers",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
)
# This revision's own frozen shape — no ``seq`` column — reshaped by 0014's route-event
# tiebreak. Not imported from schema.py (see the module docstring).
_route_created = sa.Table(
    "route_created",
    _frozen_metadata,
    sa.Column("route_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("workspace_id", sa.String, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
)
_route_released = sa.Table(
    "route_released",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("released_at", UtcDateTime, nullable=False),
)
# This revision's own frozen shape — no ``to_graph_model`` column — reshaped by the
# edge-target-graph-model revision (issue #90). Not imported from schema.py (see the
# module docstring). Its ``from_node_id``/``choice_id`` FKs (to ``graph_nodes`` /
# ``graph_choices``, both created below from the real schema.py import, unreshaped) each
# need a same-MetaData resolution stub here — never added to ``_TABLES``, never created.
sa.Table(
    "graph_nodes",
    _frozen_metadata,
    sa.Column("node_id", sa.String, primary_key=True),
)
sa.Table(
    "graph_choices",
    _frozen_metadata,
    sa.Column("choice_id", sa.String, primary_key=True),
)
_graph_edges = sa.Table(
    "graph_edges",
    _frozen_metadata,
    sa.Column("edge_id", sa.String, primary_key=True),
    sa.Column("from_node_id", sa.String, sa.ForeignKey("graph_nodes.node_id"), nullable=False),
    sa.Column("choice_id", sa.String, sa.ForeignKey("graph_choices.choice_id"), nullable=False),
    sa.Column("to_node_name", sa.String, nullable=False),
    sa.Column("prompt_addendum", sa.Text, nullable=True),
)
# This revision's own frozen shape — no ``graph_id`` column — reshaped by the
# transition-graph-id revision (issue #90). Not imported from schema.py (see the module
# docstring). Its ``chunk_id`` FK resolves against the same-MetaData ``_chunks`` stub above.
_transitions = sa.Table(
    "transitions",
    _frozen_metadata,
    sa.Column("transition_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("from_node_id", sa.String, nullable=True),
    sa.Column("to_node_id", sa.String, nullable=False),
    sa.Column("choice_name", sa.String, nullable=True),
    sa.Column("decision_id", sa.String, nullable=True),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)
# This revision's own frozen shape — no ``stopped_by`` column — reshaped by
# ``20260719_2000_hub_chunk_stopped_by`` (issue #118). Not imported from schema.py
# (see the module docstring). Its ``chunk_id`` FK resolves against the same-MetaData
# ``_chunks`` stub above.
_chunk_stopped = sa.Table(
    "chunk_stopped",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("stopped_at", UtcDateTime, nullable=False),
)

# Parents before children so the FK constraints resolve.
_TABLES = [
    graphs,
    graph_nodes,
    graph_choices,
    _graph_edges,
    _chunks,
    _chunk_pm_pointers,
    _transitions,
    artifacts,
    lease_facts,
    _route_created,
    route_environments,
    _route_released,
    delivery_repo_landed,
    delivery_landed,
    _chunk_stopped,
    escalations,
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
