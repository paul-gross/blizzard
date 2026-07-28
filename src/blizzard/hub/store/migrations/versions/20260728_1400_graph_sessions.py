"""graph sessions — the graph-level named-session declarations (hub store tree)

Issue #144 gives a graph a top-level ``sessions:`` map: named declarations carrying a
prioritized ``model`` preference list, an ``effort`` value, and ``rotate:`` thresholds,
which nodes reference by name (``fresh:<name>`` / ``resume:<name>``). ``graph_sessions``
holds one row per declaration, immutable with the graph that owns it — ``graphs`` and
everything under it stays insert-only.

A declaration mints no id: ``(graph_id, name)`` is the primary key, because ``name`` is
what a node's reference and the runner's pool lookup both key on.

No backfill and none possible — a graph minted before this revision declared no sessions,
so it reads zero rows, which is exactly what it meant. Every pre-#144 graph keeps today's
behavior: its nodes carry only the bare ``fresh``/``resume``/``resume:<node>`` forms,
none of which consult this table.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260728_1400_hub_graph_sessions
Revises: 20260728_1230_hub_chunk_migration_source
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import graph_sessions

revision: str = "20260728_1400_hub_graph_sessions"
down_revision: str | None = "20260728_1230_hub_chunk_migration_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (graph_sessions,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
