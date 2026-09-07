"""drop the unread node ``mode`` column (blizzard#52) — parsed, carried, and served, but never read

Revision ID: 20260907_0900_drop_node_mode
Revises: 20260906_1130_routine_scopes_join
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0900_drop_node_mode"
down_revision: str | None = "20260906_1130_routine_scopes_join"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("graph_nodes")}
    if "mode" not in columns:
        return  # already reshaped — guards the revision itself, not per-row

    with op.batch_alter_table("graph_nodes") as batch:
        batch.drop_column("mode")


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("graph_nodes")}
    if "mode" in columns:
        return  # already the pre-drop shape

    with op.batch_alter_table("graph_nodes") as batch:
        batch.add_column(sa.Column("mode", sa.String, nullable=True))
