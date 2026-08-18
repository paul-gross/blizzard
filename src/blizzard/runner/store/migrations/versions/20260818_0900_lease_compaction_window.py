"""lease_context compaction window — the resolved `--autocompact` value (runner store tree, #343)

One guarded, nullable column on ``lease_context``: un-backfilled, so NULL means unknown.
Revision ID: 20260818_0900_runner_lease_compaction_window
Revises: 20260817_1000_runner_graph_artifacts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0900_runner_lease_compaction_window"
down_revision: str | None = "20260817_1000_runner_graph_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lease_context"
_COLUMNS = ("resolved_compaction_window",)


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    for name in _COLUMNS:
        if name not in present:
            op.add_column(_TABLE, sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        for name in _COLUMNS:
            if name in present:
                batch.drop_column(name)
