"""store-and-forward high-water mark (hub store tree) — the greatest per-runner sequence already
applied, so a replayed fact is re-acked rather than re-applied. Creates exactly that one table.

Revision ID: 20260713_1635_hub_runner_high_water
Revises: 20260713_1424_hub_escalation_takeover
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import runner_high_water

revision: str = "20260713_1635_hub_runner_high_water"
down_revision: str | None = "20260713_1424_hub_escalation_takeover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    runner_high_water.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    runner_high_water.drop(op.get_bind(), checkfirst=False)
