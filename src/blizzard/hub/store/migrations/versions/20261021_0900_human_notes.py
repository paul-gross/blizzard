"""human notes — a person's note on a resolution, requeue and restart, and who requeued.

Revision ID: 20261021_0900_human_notes
Revises: 20261019_0900_runner_version
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0900_human_notes"
down_revision: str | None = "20261019_0900_runner_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: dict[str, tuple[tuple[str, sa.types.TypeEngine[Any]], ...]] = {
    "decision_resolutions": (("note", sa.Text()),),
    "requeues": (("requeued_by", sa.String()), ("note", sa.Text())),
    "chunk_restarts": (("note", sa.Text()),),
}


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps the unnamed ``CHECK`` its own reflection drops."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    bind = op.get_bind()
    for table, columns in _COLUMNS.items():
        held = _columns(bind, table)
        missing = [(name, kind) for name, kind in columns if name not in held]
        if not missing:
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            for name, kind in missing:
                batch.add_column(sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table, columns in _COLUMNS.items():
        held = _columns(bind, table)
        present = [name for name, _ in columns if name in held]
        if not present:
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            for name in present:
                batch.drop_column(name)
