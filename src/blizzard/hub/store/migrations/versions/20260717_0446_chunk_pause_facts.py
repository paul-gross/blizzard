"""chunk pause facts — an operator-level brake over one chunk; append-only,
newest-fact-wins, mirroring ``runner_pause_facts`` (hub store tree, issue #46)

Revision ID: 20260717_0446_hub_chunk_pause_facts
Revises: 20260716_2207_hub_route_seq_tiebreak
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_pause_facts

revision: str = "20260717_0446_hub_chunk_pause_facts"
down_revision: str | None = "20260716_2207_hub_route_seq_tiebreak"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (chunk_pause_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
