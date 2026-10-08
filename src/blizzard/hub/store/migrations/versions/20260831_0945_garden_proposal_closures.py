"""garden_proposal_closures — one closure fact per pass or accept. One
new table.

Revision ID: 20260831_0945_garden_proposal_closures
Revises: 20260831_0930_finding_sets_routine_name
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260831_0945_garden_proposal_closures"
down_revision: str | None = "20260831_0930_finding_sets_routine_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``garden_proposal_closures`` is a local literal of the hub store's declared
# shape. ``garden_proposals`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "garden_proposals",
    _frozen_metadata,
    sa.Column("proposal_id", sa.String, primary_key=True),
)
_garden_proposal_closures = sa.Table(
    "garden_proposal_closures",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("proposal_id", sa.String, sa.ForeignKey("garden_proposals.proposal_id"), nullable=False),
    sa.Column("closure", sa.String, nullable=False),
    sa.Column("reason", sa.String, nullable=True),
    sa.Column("closed_by", sa.String, nullable=False),
    sa.Column("closed_at", UtcDateTime, nullable=False),
    sa.Column("item_outcome", sa.String, nullable=True),
    sa.Column("source", sa.String, nullable=True),
    sa.Column("ref", sa.String, nullable=True),
    sa.UniqueConstraint("proposal_id", name="uq_garden_proposal_closures_proposal_id"),
)
sa.Index(
    "ix_garden_proposal_closures_source_ref",
    _garden_proposal_closures.c.source,
    _garden_proposal_closures.c.ref,
    unique=True,
)

_TABLES = (_garden_proposal_closures,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
