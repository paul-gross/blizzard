"""runner-reported local pause facts — the runner's own brake, made visible (hub store tree)

Issue #43 gives the runner a brake of its own (``PATCH /runner``), reported up through the
outbound buffer. The hub holds those facts here so the board can render *which*
brake is on: the runner declining to claim, the fleet coercing it (``runner_pause_facts``),
or both. Separate table, separate concept — the hub authors the fleet's brake and only
reads this one.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

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
