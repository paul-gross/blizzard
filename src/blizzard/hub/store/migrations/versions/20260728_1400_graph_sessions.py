"""graph sessions — one row per graph-level ``sessions:`` declaration, keyed
``(graph_id, name)`` and immutable with the graph that owns it (hub store tree, #144)

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
