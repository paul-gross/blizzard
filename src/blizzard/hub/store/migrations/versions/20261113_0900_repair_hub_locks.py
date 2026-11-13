"""Repair hub locks missing from stores stamped before the migration chain was reordered.

Revision ID: 20261113_0900_repair_hub_locks
Revises: 20261112_0900_repair_repository_targets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_0900_repair_hub_locks"
down_revision: str | None = "20261112_0900_repair_repository_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("hub_locks"):
        op.create_table("hub_locks", sa.Column("name", sa.String(), primary_key=True))
    table = sa.table("hub_locks", sa.column("name", sa.String()))
    held = set(conn.execute(sa.select(table.c.name)).scalars())
    missing = [{"name": name} for name in ("identity", "tenant_lifecycle") if name not in held]
    if missing:
        op.bulk_insert(table, missing)


def downgrade() -> None:
    """Keep the table and rows, which the parent lineage already owns."""
