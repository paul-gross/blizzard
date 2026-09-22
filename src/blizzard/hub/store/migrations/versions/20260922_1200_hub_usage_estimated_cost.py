"""``usage_facts.estimated_cost_usd`` — one guarded, nullable column, un-backfilled: NULL
declares unknown, never a value.

Revision ID: 20260922_1200_hub_usage_estimated_cost
Revises: 20260922_1100_runner_external_usage_misses
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260922_1200_hub_usage_estimated_cost"
down_revision: str | None = "20260922_1100_runner_external_usage_misses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USAGE_FACTS = "usage_facts"
_COLUMN = "estimated_cost_usd"


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind, _USAGE_FACTS):
        op.add_column(_USAGE_FACTS, sa.Column(_COLUMN, sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind, _USAGE_FACTS):
        with op.batch_alter_table(_USAGE_FACTS) as batch:
            batch.drop_column(_COLUMN)
