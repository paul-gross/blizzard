"""The fact-egress cursor's fact table, and a ``(recorded_at, id)`` index on ``usage_facts`` for the
sweep's read of usage past a position. It supersedes ``ix_usage_facts_recorded_at``, which its leading
column serves.

Revision ID: 20261003_0900_egress_cursor
Revises: 20261001_1000_trace_cursor
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261003_0900_egress_cursor"
down_revision: str | None = "20261001_1000_trace_cursor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURSOR = "egress_cursor"
_CURSOR_INDEX = "ix_egress_cursor_dataset_recorded_at_id"
_USAGE = "usage_facts"
_USAGE_INDEX = "ix_usage_facts_recorded_at_id"
_SUPERSEDED_USAGE_INDEX = "ix_usage_facts_recorded_at"


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _CURSOR not in sa.inspect(bind).get_table_names():
        op.create_table(
            _CURSOR,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("dataset", sa.String, nullable=False),
            sa.Column("position_at", UtcDateTime, nullable=True),
            sa.Column("chunk_id", sa.String, nullable=True),
            sa.Column("epoch", sa.Integer, nullable=True),
            sa.Column("decision_id", sa.String, nullable=True),
            sa.Column("usage_recorded_at", UtcDateTime, nullable=False),
            sa.Column("usage_id", sa.Integer, nullable=False),
            sa.Column("row_count", sa.Integer, nullable=False),
            sa.Column("files", sa.Text, nullable=False),
            sa.Column("recorded_at", UtcDateTime, nullable=False),
        )
    if _CURSOR_INDEX not in _index_names(bind, _CURSOR):
        op.create_index(_CURSOR_INDEX, _CURSOR, ["dataset", "recorded_at", "id"])
    if _USAGE_INDEX not in _index_names(bind, _USAGE):
        op.create_index(_USAGE_INDEX, _USAGE, ["recorded_at", "id"])
    if _SUPERSEDED_USAGE_INDEX in _index_names(bind, _USAGE):
        op.drop_index(_SUPERSEDED_USAGE_INDEX, table_name=_USAGE)


def downgrade() -> None:
    bind = op.get_bind()
    if _USAGE_INDEX in _index_names(bind, _USAGE):
        op.drop_index(_USAGE_INDEX, table_name=_USAGE)
    if _SUPERSEDED_USAGE_INDEX not in _index_names(bind, _USAGE):
        op.create_index(_SUPERSEDED_USAGE_INDEX, _USAGE, ["recorded_at"])
    if _CURSOR in sa.inspect(bind).get_table_names():
        op.drop_table(_CURSOR)
