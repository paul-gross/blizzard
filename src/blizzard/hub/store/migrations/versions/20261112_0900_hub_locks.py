"""hub locks — the table of lock-only rows for concepts no tenant owns.

Creates `hub_locks` and seeds the `identity` and `tenant_lifecycle` rows; a rerun skips both. The downgrade drops it.
Revision ID: 20261112_0900_hub_locks
Revises: 20261111_1000_chunk_repository_targets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0900_hub_locks"
down_revision: str | None = "20261111_1000_chunk_repository_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "hub_locks"
_SEEDS = ("identity", "tenant_lifecycle")


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table(_TABLE):
        op.create_table(_TABLE, sa.Column("name", sa.String(), primary_key=True))
    table = sa.table(_TABLE, sa.column("name", sa.String()))
    held = set(conn.execute(sa.select(table.c.name)).scalars())
    missing = [{"name": name} for name in _SEEDS if name not in held]
    if missing:
        op.bulk_insert(table, missing)


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
