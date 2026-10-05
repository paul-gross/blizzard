"""keyed_locks — lock-only rows a check-then-act decision locks when it has no existing row to lock.

Revision ID: 20261005_0900_keyed_locks
Revises: 20261004_1400_forge_annotation_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0900_keyed_locks"
down_revision: str | None = "20261004_1400_forge_annotation_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "keyed_locks"


def upgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("namespace", sa.String, primary_key=True),
            sa.Column("key", sa.String, primary_key=True),
        )


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(_TABLE)
