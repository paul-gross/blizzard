"""chunk pause facts — an operator-level brake over one specific chunk (hub store tree)

Issue #46 gives an operator a per-chunk pause, orthogonal to the runner's own brake
(``runner_pause_facts``) and to detach (which gives up the claim). ``chunk_pause_facts``
mirrors ``runner_pause_facts`` exactly: append-only, newest-fact-wins.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

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
