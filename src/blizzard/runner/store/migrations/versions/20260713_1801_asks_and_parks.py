"""asks and park/resume fact tables (runner store tree)

Revision ID: 20260713_1801_runner_asks_and_parks
Revises: 20260713_1635_runner_heartbeats
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.runner.store.schema import park_facts, park_resumes

revision: str = "20260713_1801_runner_asks_and_parks"
down_revision: str | None = "20260713_1635_runner_heartbeats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_asks = sa.Table(
    "asks",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("question_id", sa.String(), nullable=False),
    sa.Column("question", sa.Text(), nullable=False),
    sa.Column("options", sa.Text(), nullable=False),
    sa.Column("session_id", sa.String(), nullable=True),
    sa.Column("asked_at", sa.DateTime(), nullable=False),
)
_TABLES = (_asks, park_facts, park_resumes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
