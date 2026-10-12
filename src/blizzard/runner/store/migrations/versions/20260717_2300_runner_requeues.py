"""requeues — the operator's explicit hand-back after a human hold

One brand-new table, no reshape: the runner tree declares no ForeignKeys at all.
Revision ID: 20260717_2300_runner_requeues
Revises: 20260717_2245_runner_takeovers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260717_2300_runner_requeues"
down_revision: str | None = "20260717_2245_runner_takeovers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_requeues = sa.Table(
    "requeues",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("requeued_at", UtcDateTime(), nullable=False),
)


def upgrade() -> None:
    bind = op.get_bind()
    _requeues.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    _requeues.drop(bind, checkfirst=True)
