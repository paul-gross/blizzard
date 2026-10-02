"""usage_facts estimated cost — the zero-cost steps' estimate kept on the row (runner store tree)

One guarded, nullable column on ``usage_facts``: un-backfilled, so a row written before it reads NULL.
Revision ID: 20261002_1000_runner_usage_facts_estimated_cost
Revises: 20260925_1000_runner_lease_graph_name_work_refs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_1000_runner_usage_facts_estimated_cost"
down_revision: str | None = "20260925_1000_runner_lease_graph_name_work_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "usage_facts"
_COLUMN = "estimated_cost_usd"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _columns(op.get_bind()):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Float(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
