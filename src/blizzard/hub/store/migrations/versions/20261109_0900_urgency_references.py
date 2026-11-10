"""urgency references — a routine's default urgency and a chore's override.

Adds nullable `routines.default_urgency_id` and `cadence_chores.urgency_id`, no foreign keys or backfill.
Revision ID: 20261109_0900_urgency_references
Revises: 20261108_0900_urgencies
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_0900_urgency_references"
down_revision: str | None = "20261108_0900_urgencies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Each nullable column this revision adds: table, name.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("routines", "default_urgency_id"),
    ("cadence_chores", "urgency_id"),
)


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps what its own reflection drops."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    bind = op.get_bind()
    for table, name in _COLUMNS:
        if name not in _columns(bind, table):
            op.add_column(table, sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table, name in _COLUMNS:
        if name not in _columns(bind, table):
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            batch.drop_column(name)
