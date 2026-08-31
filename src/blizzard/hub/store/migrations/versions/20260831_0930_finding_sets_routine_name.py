"""finding_sets.routine_name — a set's own routine, by name (blizzard#392, D5). Non-null,
server-defaulted; indexed alongside `scope_slug`, the pair a delta run's baseline reads by.

Revision ID: 20260831_0930_finding_sets_routine_name
Revises: 20260831_0900_work_items_routine_run
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0930_finding_sets_routine_name"
down_revision: str | None = "20260831_0900_work_items_routine_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "finding_sets"
_COLUMN = "routine_name"
_INDEX = "ix_finding_sets_routine_scope"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=False, server_default=""))
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, ["routine_name", "scope_slug"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
