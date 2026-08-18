"""graph sessions — one row per graph-level ``sessions:`` declaration, keyed
``(graph_id, name)`` and immutable with the graph that owns it (hub store tree, #144)

Revision ID: 20260728_1400_hub_graph_sessions
Revises: 20260728_1230_hub_chunk_migration_source
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_1400_hub_graph_sessions"
down_revision: str | None = "20260728_1230_hub_chunk_migration_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no `compaction_window` column, added by a later
# revision (`bzh:frozen-revisions`). The `graphs` entry below is an FK-resolution stub:
# never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "graphs",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, primary_key=True),
)
_graph_sessions = sa.Table(
    "graph_sessions",
    _frozen_metadata,
    sa.Column("graph_id", sa.String, sa.ForeignKey("graphs.graph_id"), primary_key=True),
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("model", sa.Text, nullable=True),
    sa.Column("effort", sa.String, nullable=True),
    sa.Column("rotate_max_context_tokens", sa.Integer, nullable=True),
    sa.Column("rotate_max_transcript_bytes", sa.Integer, nullable=True),
    sa.Column("rotate_max_invocations", sa.Integer, nullable=True),
)

_TABLES = [_graph_sessions]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
