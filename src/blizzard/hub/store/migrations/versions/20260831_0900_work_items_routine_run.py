"""work_items.routine_name/scope_slug/run_mode — a routine run's own indexed values
(blizzard#392), nullable. ``scope_slug`` carries no ``ForeignKey`` (SQLite cannot drop one).

Revision ID: 20260831_0900_work_items_routine_run
Revises: 20260830_2015_garden_proposals_source_artifact
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0900_work_items_routine_run"
down_revision: str | None = "20260830_2015_garden_proposals_source_artifact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "work_items"
_INDEX = "ix_work_items_routine_scope"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    if "routine_name" not in present:
        op.add_column(_TABLE, sa.Column("routine_name", sa.String(), nullable=True))
    if "scope_slug" not in present:
        op.add_column(_TABLE, sa.Column("scope_slug", sa.String(), nullable=True))
    if "run_mode" not in present:
        op.add_column(_TABLE, sa.Column("run_mode", sa.String(), nullable=True))
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, ["routine_name", "scope_slug"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    present = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        for name in ("routine_name", "scope_slug", "run_mode"):
            if name in present:
                batch.drop_column(name)
