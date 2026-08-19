"""chunk_completed — an operator's manual chunk completion (issue #294, hub store tree).
One new table, ``checkfirst`` so a fresh ``base -> head`` and an upgrade converge.

Revision ID: 20260818_2100_chunk_completed
Revises: 20260818_0900_hub_graph_sessions_compaction_window
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_completed

revision: str = "20260818_2100_chunk_completed"
down_revision: str | None = "20260818_0900_hub_graph_sessions_compaction_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (chunk_completed,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
