"""queue shaping — creates ``queue_positions`` (the append-only ready-queue ordering
fact) and ``chunk_grouped`` (a merged-away chunk naming its survivor)

Revision ID: 20260713_1946_hub_queue_shaping
Revises: 20260713_1803_hub_gate_decisions
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_grouped, queue_positions

revision: str = "20260713_1946_hub_queue_shaping"
down_revision: str | None = "20260713_1803_hub_gate_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [queue_positions, chunk_grouped]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
