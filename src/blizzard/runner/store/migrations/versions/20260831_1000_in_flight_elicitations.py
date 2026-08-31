"""in-flight judgement elicitations — the detached launch/collect record (blizzard#443)

Revision ID: 20260831_1000_runner_in_flight_elicitations
Revises: 20260818_0900_runner_lease_compaction_window
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260831_1000_runner_in_flight_elicitations"
down_revision: str | None = "20260818_0900_runner_lease_compaction_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision's own shape (`bzh:frozen-revisions`) — NOT imported from live
# `schema.py`, which may reshape this table in a later revision.
_frozen_metadata = sa.MetaData()

in_flight_elicitations = sa.Table(
    "in_flight_elicitations",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("pid", sa.Integer, nullable=True),
    sa.Column("process_start_time", sa.String, nullable=True),
    sa.Column("output_path", sa.String, nullable=False),
    sa.Column("first_launched_at", UtcDateTime, nullable=False),
    sa.Column("relaunch_count", sa.Integer, nullable=False),
)


def upgrade() -> None:
    bind = op.get_bind()
    in_flight_elicitations.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    in_flight_elicitations.drop(bind, checkfirst=True)
