"""runner-side nudge-once fact — ``nudge_facts``, at most one row per ``(lease_id,
epoch)``, written before the resume it guards (runner store tree)

Revision ID: 20260719_1100_runner_nudge_facts
Revises: 20260719_1000_runner_attachments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260719_1100_runner_nudge_facts"
down_revision: str | None = "20260719_1000_runner_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_nudge_facts = sa.Table(
    "nudge_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("nudged_at", UtcDateTime(), nullable=False),
)

_TABLES = (_nudge_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
