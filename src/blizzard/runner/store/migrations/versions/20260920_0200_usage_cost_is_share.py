"""Mark whether a usage fact's ``cost_usd`` holds that invocation's own share.

Rows predating the session-scoped reading hold whatever the harness reported, which for a
session-scoped harness is a running total; they are marked ``0`` so the basis reads them
through ``reported_cost_usd`` instead of summing totals as if they were shares.
Revision ID: 20260920_0200_usage_cost_is_share
Revises: 20260920_0100_usage_reported_cost
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0200_usage_cost_is_share"
down_revision = "20260920_0100_usage_reported_cost"
branch_labels = None
depends_on = None

_USAGE_FACTS = "usage_facts"
_IS_SHARE = "cost_is_share"


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, _USAGE_FACTS, _IS_SHARE):
        op.add_column(
            _USAGE_FACTS,
            sa.Column(_IS_SHARE, sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_column(bind, _USAGE_FACTS, _IS_SHARE):
        with op.batch_alter_table(_USAGE_FACTS) as batch:
            batch.drop_column(_IS_SHARE)
