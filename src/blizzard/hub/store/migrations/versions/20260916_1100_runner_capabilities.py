"""Runner capability snapshot — one nullable, un-backfilled column (blizzard#433).

Revision ID: 20260916_1100_hub_runner_capabilities
Revises: 20260916_1000_hub_authored_harnesses
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_1100_hub_runner_capabilities"
down_revision: str | None = "20260916_1000_hub_authored_harnesses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMN = "capabilities"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
