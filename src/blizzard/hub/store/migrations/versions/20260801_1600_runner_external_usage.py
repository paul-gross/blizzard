"""runner external subscription usage (issue #218, hub store tree) — one refresh-in-place row per runner
holding its newest sampled rate-limit windows. Creates that one table, ``checkfirst``.

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
