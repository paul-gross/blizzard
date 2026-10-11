"""runner-side check results + checks-ran guard — _check_results, checks_ran (runner store tree)

``checks_ran`` is written AFTER the ``check_results`` rows: unset on recovery ⇒ re-run all.
Revision ID: 20260725_1200_runner_check_results
Revises: 20260722_1000_runner_git_commit_declarations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260725_1200_runner_check_results"
down_revision: str | None = "20260722_1000_runner_git_commit_declarations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_check_results = sa.Table(
    "check_results",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("node_id", sa.String(), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("command", sa.Text(), nullable=False),
    sa.Column("passed", sa.Boolean(), nullable=False),
    sa.Column("output_tail", sa.Text(), nullable=False),
    sa.Column("ran_at", UtcDateTime(), nullable=False),
)
_checks_ran = sa.Table(
    "checks_ran",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("ran_at", UtcDateTime(), nullable=False),
)

_TABLES = (_check_results, _checks_ran)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
