"""pause parks — a table pair separate from ``park_facts``/``park_resumes``,
since a nullable ``question_id`` there would make ``NOT IN (... NULL)`` swallow every row

Revision ID: 20260717_0446_runner_pause_parks
Revises: 20260716_1511_runner_local_pause
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260717_0446_runner_pause_parks"
down_revision: str | None = "20260716_1511_runner_local_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_pause_park_resumes = sa.Table(
    "pause_park_resumes",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("resumed_at", UtcDateTime(), nullable=False),
)
_pause_parks = sa.Table(
    "pause_parks",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("parked_at", UtcDateTime(), nullable=False),
)

_TABLES = (_pause_parks, _pause_park_resumes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
