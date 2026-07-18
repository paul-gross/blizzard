"""usage facts — harness cost/token telemetry per invocation, hub-side (issue #59, hub store tree)

Epic #57's cost-observability half, landed at the hub: one append-only row per harness
invocation whose usage the runner reported up over ``usage.recorded``, ridden on the
same store-and-forward rails ``lease_facts`` uses. Usage is a fact, never a stored
aggregate (``bzh:facts-not-status``) — a chunk's total is derived at read time by
summing these rows (``derive_chunk_usage``, ``hub/domain/work.py``). Deliberately **not**
epoch-fenced: a stale-epoch row still lands, since it is real spend by a fenced-out
zombie attempt, not a rejected transition (contrast the completion path's epoch fence).

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260717_2330_hub_usage_facts
Revises: 20260717_2318_hub_chunk_model_selection
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import usage_facts

revision: str = "20260717_2330_hub_usage_facts"
down_revision: str | None = "20260717_2318_hub_chunk_model_selection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (usage_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
