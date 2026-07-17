"""heartbeats fact table (runner store tree)

P7 progress detection adds the ``heartbeats`` fact table 0002/0003 do not carry: a
worker beats via its ``PostToolUse`` hook, and REAP reads the last beat to catch a
stalled-but-alive worker. The ``outbound_buffer.lease_id``
column that store-and-forward's completion flush needs is part of the ``outbound_buffer``
definition in ``blizzard.runner.store.schema`` — created with the table in 0002, the
same live-schema pattern these revisions use (each creates a subset of the schema's
tables). This revision creates exactly the one new table.

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
