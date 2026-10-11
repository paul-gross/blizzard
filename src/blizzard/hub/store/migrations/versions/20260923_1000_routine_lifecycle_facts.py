"""routine lifecycle facts — a reversible retire/re-enable brake over one routine_id.
Append-only, newest-fact-wins, created ``checkfirst``; ``routines`` stays untouched.

Revision ID: 20260923_1000_routine_lifecycle_facts
Revises: 20260922_1200_hub_usage_estimated_cost
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260923_1000_routine_lifecycle_facts"
down_revision: str | None = "20260922_1200_hub_usage_estimated_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``routine_lifecycle_facts`` is a local literal of the hub store's declared
# shape. ``routines`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "routines",
    _frozen_metadata,
    sa.Column("routine_id", sa.String, primary_key=True),
)
_routine_lifecycle_facts = sa.Table(
    "routine_lifecycle_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("routine_id", sa.String, sa.ForeignKey("routines.routine_id"), nullable=False),
    sa.Column("retired", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)

_TABLES = (_routine_lifecycle_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
