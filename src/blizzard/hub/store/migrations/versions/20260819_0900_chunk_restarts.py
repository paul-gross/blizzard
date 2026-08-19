"""chunk_restarts — an operator's forced move of a chunk onto a node (issue #370, hub store
tree). One new table, ``checkfirst`` so a fresh ``base -> head`` and an upgrade converge.

Revision ID: 20260819_0900_chunk_restarts
Revises: 20260818_2100_chunk_completed
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_restarts

revision: str = "20260819_0900_chunk_restarts"
down_revision: str | None = "20260818_2100_chunk_completed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (chunk_restarts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
