"""config_import_facts — when a file-configured hub's legacy keys were carried over to its records.

Revision ID: 20261006_0900_config_import_facts
Revises: 20261005_1000_scope_routine_revisions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261006_0900_config_import_facts"
down_revision: str | None = "20261005_1000_scope_routine_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "config_import_facts"


def upgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("imported_at", UtcDateTime, nullable=False),
            sa.Column("actor", sa.String, nullable=False),
            sa.Column("read", sa.Text, nullable=False),
        )


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(_TABLE)
