"""Keep a usage fact's harness-reported cost figure beside its derived invocation cost.

Every historical row's ``cost_usd`` is that figure, so the backfill is a copy.
Revision ID: 20260920_0100_usage_reported_cost
Revises: 20260919_1400_selftest_results
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0100_usage_reported_cost"
down_revision = "20260919_1400_selftest_results"
branch_labels = None
depends_on = None

_USAGE_FACTS = "usage_facts"
_REPORTED = "reported_cost_usd"


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, _USAGE_FACTS, _REPORTED):
        op.add_column(_USAGE_FACTS, sa.Column(_REPORTED, sa.Float(), nullable=True))
        op.execute(sa.text(f"UPDATE {_USAGE_FACTS} SET {_REPORTED} = cost_usd"))


def downgrade() -> None:
    bind = op.get_bind()

    if _has_column(bind, _USAGE_FACTS, _REPORTED):
        with op.batch_alter_table(_USAGE_FACTS) as batch:
            batch.drop_column(_REPORTED)
