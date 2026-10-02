"""The lease trace sweep's cursor and failure-latch fact tables, and a ``(closed_at, lease_id)``
index on ``lease_closures`` for its window read (runner store tree).

Revision ID: 20261002_1100_runner_trace_cursor
Revises: 20261002_1000_runner_usage_facts_estimated_cost
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261002_1100_runner_trace_cursor"
down_revision: str | None = "20261002_1000_runner_usage_facts_estimated_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURSOR = "trace_cursor"
_LATCH = "trace_export_latch"
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_trace_cursor_recorded_at_id", _CURSOR, ("recorded_at", "id")),
    ("ix_trace_export_latch_recorded_at_id", _LATCH, ("recorded_at", "id")),
    ("ix_lease_closures_closed_at_lease_id", "lease_closures", ("closed_at", "lease_id")),
)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if _CURSOR not in tables:
        op.create_table(
            _CURSOR,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("position_at", UtcDateTime, nullable=False),
            sa.Column("lease_id", sa.String, nullable=False),
            sa.Column("span_count", sa.Integer, nullable=False),
            sa.Column("recorded_at", UtcDateTime, nullable=False),
        )
    if _LATCH not in tables:
        op.create_table(
            _LATCH,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("kind", sa.String, nullable=False),
            sa.Column("recorded_at", UtcDateTime, nullable=False),
        )
    for name, table, columns in _INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "ix_lease_closures_closed_at_lease_id" in _index_names(bind, "lease_closures"):
        op.drop_index("ix_lease_closures_closed_at_lease_id", table_name="lease_closures")
    for table in (_LATCH, _CURSOR):
        if table in tables:
            op.drop_table(table)
