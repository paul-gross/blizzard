"""escalation closures — one append-only row per local escalation the hub has since
stopped, the supersession no later lease mint can supply (runner store tree)

Revision ID: 20260811_1400_runner_escalation_closures
Revises: 20260811_1000_runner_context_samples
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260811_1400_runner_escalation_closures"
down_revision: str | None = "20260811_1000_runner_context_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_escalation_closures = sa.Table(
    "escalation_closures",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("reason", sa.String(), nullable=False),
    sa.Column("closed_at", UtcDateTime(), nullable=False),
)

_TABLES = (_escalation_closures,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
