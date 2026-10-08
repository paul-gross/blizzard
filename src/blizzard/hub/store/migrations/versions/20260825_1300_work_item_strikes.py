"""work_item_strikes — a gate resolution's refusal of a proposal, before
materialization ever judges it. One new table, FROZEN (``bzh:frozen-revisions``) since a
later revision drops it.

Revision ID: 20260825_1300_work_item_strikes
Revises: 20260825_1250_hub_transitions_to_node_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260825_1300_work_item_strikes"
down_revision: str | None = "20260825_1250_hub_transitions_to_node_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The `work_item_proposals` and `decisions` entries below are FK-resolution stubs: never
# created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table("work_item_proposals", _frozen_metadata, sa.Column("proposal_id", sa.String, primary_key=True))
sa.Table("decisions", _frozen_metadata, sa.Column("decision_id", sa.String, primary_key=True))
_work_item_strikes = sa.Table(
    "work_item_strikes",
    _frozen_metadata,
    sa.Column("proposal_id", sa.String, sa.ForeignKey("work_item_proposals.proposal_id"), primary_key=True),
    sa.Column("decision_id", sa.String, sa.ForeignKey("decisions.decision_id"), nullable=False),
    sa.Column("struck_by", sa.String, nullable=False),
    sa.Column("struck_at", UtcDateTime, nullable=False),
)

_TABLES = (_work_item_strikes,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
