"""Scope and routine revisions — each becomes a configured record with a revision.

Every existing row starts at revision 1.
Revision ID: 20261005_1000_scope_routine_revisions
Revises: 20261005_0900_keyed_locks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_1000_scope_routine_revisions"
down_revision: str | None = "20261005_0900_keyed_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("scopes", "routines")
_COLUMN = "revision"


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if _COLUMN not in _columns(bind, table):
            op.add_column(table, sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if _COLUMN in _columns(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(_COLUMN)
