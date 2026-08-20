"""work_items — hub-owned work items and their per-source ref sequence (issue #357,
hub store tree). Two new tables, ``checkfirst`` so a fresh ``base -> head`` and an
upgrade converge.

Revision ID: 20260819_1100_work_items
Revises: 20260819_0900_chunk_restarts
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import work_item_sequence, work_items

revision: str = "20260819_1100_work_items"
down_revision: str | None = "20260819_0900_chunk_restarts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (work_items, work_item_sequence)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
