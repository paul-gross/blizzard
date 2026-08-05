"""graph checks gating — ``graph_nodes.checks_cwd``/``checks_timeout``,
``graph_choices.requires_checks`` (issue #114, hub store tree). Nullable, no backfill.

Revision ID: 20260725_1200_hub_graph_checks_gating
Revises: 20260722_1200_hub_artifact_forge
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_1200_hub_graph_checks_gating"
down_revision: str | None = "20260722_1200_hub_artifact_forge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NODE_COLUMNS: dict[str, sa.Column] = {
    "checks_cwd": sa.Column("checks_cwd", sa.String(), nullable=True),
    "checks_timeout": sa.Column("checks_timeout", sa.Integer(), nullable=True),
}
_CHOICE_COLUMNS: dict[str, sa.Column] = {
    "requires_checks": sa.Column("requires_checks", sa.Boolean(), nullable=True),
}


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "graph_nodes")
    for name, column in _NODE_COLUMNS.items():
        if name not in existing:
            op.add_column("graph_nodes", column)
    existing = _columns(bind, "graph_choices")
    for name, column in _CHOICE_COLUMNS.items():
        if name not in existing:
            op.add_column("graph_choices", column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "graph_choices")
    for name in _CHOICE_COLUMNS:
        if name in existing:
            op.drop_column("graph_choices", name)
    existing = _columns(bind, "graph_nodes")
    for name in _NODE_COLUMNS:
        if name in existing:
            op.drop_column("graph_nodes", name)
