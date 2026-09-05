"""runner external subscription usage (issue #218, hub store tree) — one refresh-in-place row per runner
holding its newest windows; frozen local literal, not a ``schema.py`` import (``bzh:frozen-revisions``).

Revision ID: 20260801_1600_hub_runner_external_usage
Revises: 20260801_1400_hub_work_item_closures
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260801_1600_hub_runner_external_usage"
down_revision: str | None = "20260801_1400_hub_work_item_closures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — one row per ``runner_id``, no ``slug``/``name``,
# added by 20260905_1100_runner_external_usage_slug.
_frozen_metadata = sa.MetaData()
_runner_external_usage = sa.Table(
    "runner_external_usage",
    _frozen_metadata,
    sa.Column("runner_id", sa.String, primary_key=True),
    sa.Column("sampled_at", UtcDateTime, nullable=False),
    sa.Column("windows", sa.Text, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
)

_TABLES = (_runner_external_usage,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
