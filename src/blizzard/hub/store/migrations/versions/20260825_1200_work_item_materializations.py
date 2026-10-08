"""work_item_materializations — one outcome fact per proposal. One new table, FROZEN
(``bzh:frozen-revisions``) since a later revision drops it.

Revision ID: 20260825_1200_work_item_materializations
Revises: 20260825_1150_work_item_proposals_runner_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260825_1200_work_item_materializations"
down_revision: str | None = "20260825_1150_work_item_proposals_runner_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The `work_item_proposals` entry below is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table("work_item_proposals", _frozen_metadata, sa.Column("proposal_id", sa.String, primary_key=True))
_work_item_materializations = sa.Table(
    "work_item_materializations",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("proposal_id", sa.String, sa.ForeignKey("work_item_proposals.proposal_id"), nullable=False),
    sa.Column("outcome", sa.String, nullable=False),
    sa.Column("source", sa.String, nullable=True),
    sa.Column("ref", sa.String, nullable=True),
    sa.Column("reason", sa.String, nullable=True),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("proposal_id", name="uq_work_item_materializations_proposal_id"),
)

_TABLES = (_work_item_materializations,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
