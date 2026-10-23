"""pause park cause — a nullable ``pause_parks.cause``: ``usage-limit`` for a park a usage limit wrote, NULL on every
existing row and every operator-pause park; a rerun skips it. ``downgrade()`` drops it: every park then reads as held.

Revision ID: 20261023_1000_pause_park_cause
Revises: 20261022_1000_usage_limit_floor
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_1000_pause_park_cause"
down_revision: str | None = "20261022_1000_usage_limit_floor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "pause_parks"
_COLUMN = "cause"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _columns(op.get_bind()):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
