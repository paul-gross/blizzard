"""chunk_stopped.stopped_by — who terminally stopped the chunk, nullable so a row written
before this column reads back bare (hub store tree, issue #118)

Revision ID: 20260719_2000_hub_chunk_stopped_by
Revises: 20260718_1300_hub_runner_env_capacity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_2000_hub_chunk_stopped_by"
down_revision: str | None = "20260719_0900_hub_graph_lifecycle_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunk_stopped"
_COLUMN = "stopped_by"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
