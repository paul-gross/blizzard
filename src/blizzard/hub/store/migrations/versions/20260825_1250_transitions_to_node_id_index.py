"""transitions.to_node_id index — the delivery-materialization sweep's own candidate
read (blizzard#366, hub store tree).

Every sweep pass scans ``transitions`` for ``to_node_id == RESERVED_TERMINAL``,
unindexed.
Revision ID: 20260825_1250_hub_transitions_to_node_id
Revises: 20260825_1200_work_item_materializations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_1250_hub_transitions_to_node_id"
down_revision: str | None = "20260825_1200_work_item_materializations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transitions"
_COLUMN = "to_node_id"
_INDEX = "ix_transitions_to_node_id"


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
