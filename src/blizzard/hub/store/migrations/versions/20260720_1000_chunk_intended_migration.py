"""chunk intended migration — a nullable, mutable JSON ``intended_migration`` column on
chunks, consulted (never applied eagerly) at the next transition (hub store tree, #124)

Revision ID: 20260720_1000_hub_chunk_intended_migration
Revises: 20260719_2000_hub_chunk_stopped_by
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_1000_hub_chunk_intended_migration"
down_revision: str | None = "20260719_2000_hub_chunk_stopped_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunks"
_COLUMN = "intended_migration"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
