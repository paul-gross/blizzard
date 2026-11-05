"""Drop the hub-execution slot table, the history of hub steps run before turns.

Upgrade drops the table and its rows. Downgrade recreates it empty, as the baseline made it.
Revision ID: 20261102_0900_drop_hub_exec_slot
Revises: 20261101_0900_turn_page
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261102_0900_drop_hub_exec_slot"
down_revision: str | None = "20261101_0900_turn_page"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "hub_exec_slot"
_INDEX = "ix_hub_exec_slot_tenant_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        return
    # Frozen stubs of the tables the foreign keys name, so the DDL resolves them without importing the live schema.
    stubs = sa.MetaData()
    sa.Table("chunks", stubs, sa.Column("chunk_id", sa.String(), primary_key=True))
    sa.Table("tenants", stubs, sa.Column("tenant_id", sa.String(), primary_key=True))
    slot = sa.Table(
        _TABLE,
        stubs,
        sa.Column("slot_id", sa.String(), nullable=False),
        sa.Column("holder_chunk_id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("acquired_at", UtcDateTime(), nullable=False),
        sa.Column("released_at", UtcDateTime(), nullable=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["holder_chunk_id"], ["chunks.chunk_id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], name="fk_hub_exec_slot_tenant_id"),
        sa.PrimaryKeyConstraint("slot_id"),
    )
    slot.create(bind)
    op.create_index(_INDEX, _TABLE, ["tenant_id"], unique=False)
