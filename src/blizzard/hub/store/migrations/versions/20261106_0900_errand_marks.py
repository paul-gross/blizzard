"""errand marks — the cadence errand a run was minted for, and when a scope was linked to its routine.

Adds nullable `work_item_runs.cadence_id` and `work_item_runs.chore_id` with the tenant-led index
`ix_work_item_runs_tenant_chore_scope` over `(tenant_id, chore_id, scope_id)`, and nullable `routine_scopes.linked_at`.
Every existing row reads null: a run minted by hand, a link made before the stamp. Downgrade drops all three and the
index.
Revision ID: 20261106_0900_errand_marks
Revises: 20261105_1300_cadences
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261106_0900_errand_marks"
down_revision: str | None = "20261105_1300_cadences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNS = "work_item_runs"
_LINKS = "routine_scopes"
_INDEX = "ix_work_item_runs_tenant_chore_scope"
_INDEX_COLUMNS = ("tenant_id", "chore_id", "scope_id")

#: Each nullable column this revision adds: table, name, type.
_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine[Any]], ...] = (
    (_RUNS, "cadence_id", sa.String()),
    (_RUNS, "chore_id", sa.String()),
    (_LINKS, "linked_at", UtcDateTime()),
)


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def _indexes(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


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
    for table, name, kind in _COLUMNS:
        if name not in _columns(bind, table):
            op.add_column(table, sa.Column(name, kind, nullable=True))
    if _INDEX not in _indexes(bind, _RUNS):
        op.create_index(_INDEX, _RUNS, list(_INDEX_COLUMNS))


def downgrade() -> None:
    bind = op.get_bind()
    if _INDEX in _indexes(bind, _RUNS):
        op.drop_index(_INDEX, table_name=_RUNS)
    for table in (_RUNS, _LINKS):
        present = [name for owner, name, _ in _COLUMNS if owner == table and name in _columns(bind, table)]
        if not present:
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            for name in present:
                batch.drop_column(name)
