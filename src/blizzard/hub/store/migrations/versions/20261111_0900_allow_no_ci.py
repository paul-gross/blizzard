"""Capture each repository's opt-in to landing without CI.

Revision ID: 20261111_0900_allow_no_ci
Revises: 20261112_0900_hub_locks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0900_allow_no_ci"
down_revision: str | None = "20261112_0900_hub_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("repositories", "flow_submission_terms")
_COLUMN = "allow_no_ci"


def _columns(table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in _TABLES:
        if _COLUMN not in _columns(table):
            op.add_column(table, sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in reversed(_TABLES):
        if _COLUMN not in _columns(table):
            continue
        copy = f"_alembic_tmp_{table}"[:50]
        if sa.inspect(op.get_bind()).has_table(copy):
            op.drop_table(copy)
        with op.batch_alter_table(table) as batch:
            batch.drop_column(_COLUMN)
