"""local pause facts — the runner's own brake, distinct from the hub's (runner store tree)

Locally-minted append-only facts, newest-wins; effective paused is the OR of the two brakes.
Revision ID: 20260716_1511_runner_local_pause
Revises: 20260716_0532_runner_crash_recovery_context
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260716_1511_runner_local_pause"
down_revision: str | None = "20260716_0532_runner_crash_recovery_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen literal (`bzh:frozen-revisions`): the table as this revision creates it.
_local_pause_facts = sa.Table(
    "local_pause_facts",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("runner_id", sa.String(), nullable=False),
    sa.Column("paused", sa.Boolean(), nullable=False),
    sa.Column("set_at", UtcDateTime(), nullable=False),
    sa.Column("set_by", sa.String(), nullable=False),
)

_TABLES = (_local_pause_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
