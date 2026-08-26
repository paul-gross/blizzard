"""work_item_materializations — one outcome fact per proposal (D5). One new table.

Revision ID: 20260825_1200_work_item_materializations
Revises: 20260825_1150_work_item_proposals_runner_id
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import work_item_materializations

revision: str = "20260825_1200_work_item_materializations"
down_revision: str | None = "20260825_1150_work_item_proposals_runner_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (work_item_materializations,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
