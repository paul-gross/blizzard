"""store-and-forward high-water mark (hub store tree) — the greatest per-runner sequence already
applied. Creates exactly that one table.

Revision ID: 20260713_1635_hub_runner_high_water
Revises: 20260713_1424_hub_escalation_takeover
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260713_1635_hub_runner_high_water"
down_revision: str | None = "20260713_1424_hub_escalation_takeover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``).
_frozen_metadata = sa.MetaData()
_runner_high_water = sa.Table(
    "runner_high_water",
    _frozen_metadata,
    sa.Column("runner_id", sa.String, primary_key=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
)


def upgrade() -> None:
    _runner_high_water.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    _runner_high_water.drop(op.get_bind(), checkfirst=False)
