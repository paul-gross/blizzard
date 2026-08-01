"""runner external subscription usage — the latest sampled rate-limit windows (hub store tree, issue #218)

Phase 3 lands the hub-side half of surfacing a metered harness's rate-limit window
utilization on the board: a single refresh-in-place row per runner, holding its newest
sampled snapshot (``bzh:facts-not-status``'s deliberate refresh-in-place exception,
already documented on ``runner_registrations`` — see ``hub/store/schema.py``). This
migration creates exactly the one new table and nothing else.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260801_1600_hub_runner_external_usage
Revises: 20260801_1400_hub_work_item_closures
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import runner_external_usage

revision: str = "20260801_1600_hub_runner_external_usage"
down_revision: str | None = "20260801_1400_hub_work_item_closures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (runner_external_usage,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
