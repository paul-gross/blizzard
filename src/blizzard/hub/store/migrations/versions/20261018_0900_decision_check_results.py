"""decision_check_results — a gate decision holds the checks its step ran; its resolution records an override.

``decisions`` gains ``check_results`` (a JSON list of ``{command, passed}``, empty by default) and
``decision_resolutions`` gains ``overrode_red_checks`` (false by default). Both are defaulted adds with no backfill, so
every existing decision reads as having run no checks and every existing resolution as no override. Each step inspects
first and skips what a stopped run already did.

The downgrade drops the two columns.
Revision ID: 20261018_0900_decision_check_results
Revises: 20261016_0900_chunk_demoted
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261018_0900_decision_check_results"
down_revision: str | None = "20261016_0900_chunk_demoted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Columns this revision adds: table, column name.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("decisions", "check_results"),
    ("decision_resolutions", "overrode_red_checks"),
)


def _column(name: str) -> sa.Column[Any]:
    if name == "check_results":
        return sa.Column(name, sa.Text(), nullable=False, server_default="[]")
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


def _column_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, name in _ADDED_COLUMNS:
        if name not in _column_names(bind, table):
            op.add_column(table, _column(name))


def downgrade() -> None:
    bind = op.get_bind()
    for table, name in reversed(_ADDED_COLUMNS):
        if name in _column_names(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(name)
