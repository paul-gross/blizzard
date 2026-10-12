"""heartbeats fact table (runner store tree)

Creates the one new table; ``outbound_buffer.lease_id`` ships with its own table's create.
Revision ID: 20260713_1635_runner_heartbeats
Revises: 20260713_1245_runner_lease_lifecycle
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260713_1635_runner_heartbeats"
down_revision: str | None = "20260713_1245_runner_lease_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_heartbeats = sa.Table(
    "heartbeats",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("beat_at", UtcDateTime(), nullable=False),
)


def upgrade() -> None:
    _heartbeats.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    _heartbeats.drop(op.get_bind(), checkfirst=False)
