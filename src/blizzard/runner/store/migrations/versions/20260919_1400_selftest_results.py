"""Add the durable selftest-result tables (blizzard#438).

Revision ID: 20260919_1400_selftest_results
Revises: 20260918_1000_invocation_boundaries
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision = "20260919_1400_selftest_results"
down_revision = "20260918_1000_invocation_boundaries"
branch_labels = None
depends_on = None

_RESULTS = "selftest_results"
_CHECKS = "selftest_result_checks"


def upgrade() -> None:
    bind = op.get_bind()
    existing = sa.inspect(bind).get_table_names()
    if _RESULTS not in existing:
        op.create_table(
            _RESULTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("harness_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("recorded_at", UtcDateTime(), nullable=False),
        )
    if _CHECKS not in existing:
        op.create_table(
            _CHECKS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("selftest_result_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("passed", sa.Boolean(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = sa.inspect(bind).get_table_names()
    # Children before parents (sqlite mostly won't enforce it, but do it right anyway).
    if _CHECKS in existing:
        op.drop_table(_CHECKS)
    if _RESULTS in existing:
        op.drop_table(_RESULTS)
