"""takeovers — the operator's interactive session over a parked chunk; two brand-new
tables, reshaping none (issue #52)

Revision ID: 20260717_2245_runner_takeovers
Revises: 20260717_0446_runner_pause_parks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner.store.schema import takeover_ends

revision: str = "20260717_2245_runner_takeovers"
down_revision: str | None = "20260717_0446_runner_pause_parks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_takeovers = sa.Table(
    "takeovers",
    _frozen_metadata,
    sa.Column("takeover_id", sa.String(), primary_key=True),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("lease_id", sa.String(), nullable=True),
    sa.Column("session_id", sa.String(), nullable=True),
    sa.Column("workdir", sa.String(), nullable=False),
    sa.Column("fence_epoch", sa.Integer(), nullable=True),
    sa.Column("opened_at", UtcDateTime(), nullable=False),
)
_TABLES = (_takeovers, takeover_ends)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
