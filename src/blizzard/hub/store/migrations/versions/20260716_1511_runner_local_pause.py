"""runner-reported local pause facts (hub store tree) — the runner's own brake (issue #43), a separate
table from ``runner_pause_facts``, the fleet's own brake, because they are separate concepts.

Revision ID: 20260716_1511_hub_runner_local_pause
Revises: 20260715_1817_hub_chunk_promoted
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import runner_local_pause_facts

revision: str = "20260716_1511_hub_runner_local_pause"
down_revision: str | None = "20260715_1817_hub_chunk_promoted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (runner_local_pause_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
