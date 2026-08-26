"""work_item_strikes — a gate resolution's refusal of a proposal, before
materialization ever judges it (D1, blizzard#367). One new table.

Revision ID: 20260825_1300_work_item_strikes
Revises: 20260825_1250_hub_transitions_to_node_id
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import work_item_strikes

revision: str = "20260825_1300_work_item_strikes"
down_revision: str | None = "20260825_1250_hub_transitions_to_node_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (work_item_strikes,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
