"""event_log — creates the hub's durable, append-only operational event feed: typed,
severity-ranked, clock-stamped (issue #125, hub store tree)

Revision ID: 20260721_1600_hub_event_log
Revises: 20260721_1500_hub_cli_auth_state_user
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1600_hub_event_log"
down_revision: str | None = "20260721_1500_hub_cli_auth_state_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — ``runner_id`` NOT NULL, reshaped nullable later.
# The ``chunks`` entry below is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
event_log = sa.Table(
    "event_log",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("recorded_at", sa.DateTime, nullable=False),
    sa.Column("severity", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=True),
    sa.Column("lease_id", sa.String, nullable=True),
    sa.Column("node_name", sa.String, nullable=True),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=True),
)

_TABLES = (event_log,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)
    sa.Index("ix_event_log_recorded_at", event_log.c.recorded_at).create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
