"""resume intent fact tables — ``resume_intents`` (the mark a graceful shutdown writes per
resumable lease) and ``resume_clears`` (resumed or abandoned) (runner store tree)

Revision ID: 20260714_1656_runner_resume_intents
Revises: 20260713_1946_runner_hub_control
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260714_1656_runner_resume_intents"
down_revision: str | None = "20260713_1946_runner_hub_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_resume_clears = sa.Table(
    "resume_clears",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("cleared_at", UtcDateTime(), nullable=False),
)
_resume_intents = sa.Table(
    "resume_intents",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("marked_at", UtcDateTime(), nullable=False),
)

_TABLES = (_resume_intents, _resume_clears)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
