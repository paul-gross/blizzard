"""chunk_deleted — the fact that makes an unacquired chunk ephemeral by deletion
(issue #364, hub store tree). One new table, ``checkfirst`` so a fresh ``base -> head``
and an upgrade converge.

Revision ID: 20260825_1000_chunk_deleted
Revises: 20260819_2200_chunk_restart_from_graph
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_deleted

revision: str = "20260825_1000_chunk_deleted"
down_revision: str | None = "20260819_2200_chunk_restart_from_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (chunk_deleted,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
