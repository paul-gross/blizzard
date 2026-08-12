"""walking-skeleton fact tables (hub store tree) — frozen local literals, not
``schema.py`` imports, so ``base`` recreates this revision's own column shape.

Revision ID: 20260713_1218_hub_walking_skeleton
Revises: 20260713_1112_hub_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260713_1218_hub_walking_skeleton"
down_revision: str | None = "20260713_1112_hub_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape for every table it creates — no live ``schema.py``
# import (``bzh:frozen-revisions``). Declared parents-before-children so FKs resolve.
_frozen_metadata = sa.MetaData()
_graphs = sa.Table(
    "graphs",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("entry_node_id", sa.String, nullable=False),
    sa.Column("definition_yaml", sa.Text, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
)
# This revision's own frozen shape — none of the columns later revisions add to ``graph_nodes``
# (``bzh:frozen-revisions``); the reshapes are enumerated in tests/test_store_migrations.py.
_graph_nodes = sa.Table(
    "graph_nodes",
    _frozen_metadata,
    sa.Column("node_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, sa.ForeignKey("graphs.graph_id"), nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("executor", sa.String, nullable=False),
    sa.Column("prompt", sa.Text, nullable=True),
    sa.Column("judgement_prompt", sa.Text, nullable=True),
    sa.Column("session", sa.String, nullable=False),
    sa.Column("judged_by", sa.String, nullable=False),
    sa.Column("retries_max", sa.Integer, nullable=True),
    sa.Column("retries_exhausted", sa.String, nullable=True),
    sa.Column("mode", sa.String, nullable=True),
)
# This revision's own frozen shape — no ``requires_checks`` column, added by a later revision.
_graph_choices = sa.Table(
    "graph_choices",
    _frozen_metadata,
    sa.Column("choice_id", sa.String, primary_key=True),
    sa.Column("node_id", sa.String, sa.ForeignKey("graph_nodes.node_id"), nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
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
# This revision's own frozen shape — no ``model`` column.
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
# This revision's own frozen shape — no ``graph_id`` column.
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
# This revision's own frozen shape — no ``forge`` column, added by a later revision.
_artifacts = sa.Table(
    "artifacts",
    _frozen_metadata,
    sa.Column("artifact_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("node_name", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("data", sa.Text, nullable=False),
    sa.Column("repo", sa.String, nullable=True),
    sa.Column("produced_at", UtcDateTime, nullable=False),
)
_lease_facts = sa.Table(
    "lease_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("minted_at", UtcDateTime, nullable=False),
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
_route_environments = sa.Table(
    "route_environments",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("route_id", sa.String, sa.ForeignKey("route_created.route_id"), nullable=False),
    sa.Column("environment_id", sa.String, nullable=False),
)
_route_released = sa.Table(
    "route_released",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("released_at", UtcDateTime, nullable=False),
)
_delivery_repo_landed = sa.Table(
    "delivery_repo_landed",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("commit_hash", sa.String, nullable=False),
    sa.Column("landed_at", UtcDateTime, nullable=False),
)
_delivery_landed = sa.Table(
    "delivery_landed",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("landed_at", UtcDateTime, nullable=False),
)
# This revision's own frozen shape — no ``stopped_by`` column.
_chunk_stopped = sa.Table(
    "chunk_stopped",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("stopped_at", UtcDateTime, nullable=False),
)
# This revision's own frozen shape — no ``takeover_command``/``wrapped_takeover_command``/
# ``decision_id`` columns, all added by later revisions.
_escalations = sa.Table(
    "escalations",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)

# Parents before children so the FK constraints resolve.
_TABLES = [
    _graphs,
    _graph_nodes,
    _graph_choices,
    _graph_edges,
    _chunks,
    _chunk_pm_pointers,
    _transitions,
    _artifacts,
    _lease_facts,
    _route_created,
    _route_environments,
    _route_released,
    _delivery_repo_landed,
    _delivery_landed,
    _chunk_stopped,
    _escalations,
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
