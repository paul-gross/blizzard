"""A ``(kind, recorded_at, id)`` index on ``trace_export_latch`` for the newest-failure read (runner store tree).

Revision ID: 20261002_1200_runner_trace_latch_kind_index
Revises: 20261002_1100_runner_trace_cursor
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_1200_runner_trace_latch_kind_index"
down_revision: str | None = "20261002_1100_runner_trace_cursor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "trace_export_latch"
_INDEX = "ix_trace_export_latch_kind_recorded_at_id"


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, ["kind", "recorded_at", "id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
