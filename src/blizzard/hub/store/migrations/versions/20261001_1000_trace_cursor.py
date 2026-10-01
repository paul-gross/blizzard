"""The trace export cursor's fact table, and a ``(recorded_at, id)`` index on
``epoch_owners`` for the sweep's closing-fact read.

Revision ID: 20261001_1000_trace_cursor
Revises: 20260930_1000_epoch_owners
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261001_1000_trace_cursor"
down_revision: str | None = "20260930_1000_epoch_owners"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURSOR = "trace_cursor"
_CURSOR_INDEX = "ix_trace_cursor_recorded_at_id"
_OWNERS = "epoch_owners"
_OWNERS_INDEX = "ix_epoch_owners_recorded_at_id"


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _CURSOR not in sa.inspect(bind).get_table_names():
        op.create_table(
            _CURSOR,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("position_at", UtcDateTime, nullable=False),
            sa.Column("chunk_id", sa.String, nullable=False),
            sa.Column("epoch", sa.Integer, nullable=False),
            sa.Column("decision_id", sa.String, nullable=False),
            sa.Column("span_count", sa.Integer, nullable=False),
            sa.Column("recorded_at", UtcDateTime, nullable=False),
        )
    if _CURSOR_INDEX not in _index_names(bind, _CURSOR):
        op.create_index(_CURSOR_INDEX, _CURSOR, ["recorded_at", "id"])
    if _OWNERS_INDEX not in _index_names(bind, _OWNERS):
        op.create_index(_OWNERS_INDEX, _OWNERS, ["recorded_at", "id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _OWNERS_INDEX in _index_names(bind, _OWNERS):
        op.drop_index(_OWNERS_INDEX, table_name=_OWNERS)
    if _CURSOR in sa.inspect(bind).get_table_names():
        op.drop_table(_CURSOR)
