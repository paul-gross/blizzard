"""Authored harness sets — session/chunk/routine defaults (blizzard#432).

Three guarded, nullable columns, one per table: ``graph_sessions.harnesses``,
``chunks.default_harnesses``, ``routines.default_harnesses`` — un-backfilled, so NULL
declares none, the ``graph_sessions_compaction_window`` shape.

Revision ID: 20260916_1000_hub_authored_harnesses
Revises: 20260914_1000_hub_harness_provenance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_1000_hub_authored_harnesses"
down_revision: str | None = "20260914_1000_hub_harness_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: dict[str, str] = {
    "graph_sessions": "harnesses",
    "chunks": "default_harnesses",
    "routines": "default_harnesses",
}


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, name in _COLUMNS.items():
        if name not in _columns(bind, table):
            op.add_column(table, sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table, name in _COLUMNS.items():
        if name in _columns(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(name)
