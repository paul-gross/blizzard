"""heartbeats fact table (runner store tree)

Creates the one new table; ``outbound_buffer.lease_id`` ships with its own table's create.
Revision ID: 20260713_1635_runner_heartbeats
Revises: 20260713_1245_runner_lease_lifecycle
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import heartbeats

revision: str = "20260713_1635_runner_heartbeats"
down_revision: str | None = "20260713_1245_runner_lease_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    heartbeats.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    heartbeats.drop(op.get_bind(), checkfirst=False)
