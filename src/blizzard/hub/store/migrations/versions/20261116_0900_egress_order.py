"""Order egress appends under their hub lock.

Revision ID: 20261116_0900_egress_order
Revises: 20261114_0900_app_credentials
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261116_0900_egress_order"
down_revision: str | None = "20261114_0900_app_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "egress_cursor"
_INDEX = "ix_egress_cursor_dataset_append_order_recorded_at_id"


def upgrade() -> None:
    conn = op.get_bind()
    if "append_order" not in {c["name"] for c in sa.inspect(conn).get_columns(_TABLE)}:
        op.add_column(_TABLE, sa.Column("append_order", sa.BigInteger(), nullable=False, server_default="0"))
    if _INDEX not in {i["name"] for i in sa.inspect(conn).get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["dataset", "append_order", "recorded_at", "id"])
    locks = sa.table("hub_locks", sa.column("name", sa.String()))
    if conn.execute(sa.select(locks.c.name).where(locks.c.name == "egress_cursor")).first() is None:
        conn.execute(locks.insert().values(name="egress_cursor"))


def downgrade() -> None:
    conn = op.get_bind()
    locks = sa.table("hub_locks", sa.column("name", sa.String()))
    conn.execute(locks.delete().where(locks.c.name == "egress_cursor"))
    if _INDEX in {i["name"] for i in sa.inspect(conn).get_indexes(_TABLE)}:
        op.drop_index(_INDEX, table_name=_TABLE)
    if "append_order" in {c["name"] for c in sa.inspect(conn).get_columns(_TABLE)}:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column("append_order")
