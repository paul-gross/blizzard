"""graph node produces + checks (hub store tree)

Adds two JSON ``list[str]`` columns, idempotently — a fresh store's create-all has both.
Revision ID: 20260713_1716_hub_graph_node_produces_checks
Revises: 20260713_1635_hub_runner_high_water
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_1716_hub_graph_node_produces_checks"
down_revision: str | None = "20260713_1635_hub_runner_high_water"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMNS = ("produces", "checks")


def _existing(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _existing(bind)
    for column in _COLUMNS:
        if column not in present:
            op.add_column(_TABLE, sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing(bind)
    for column in _COLUMNS:
        if column in present:
            op.drop_column(_TABLE, column)
