"""Declared subscription roster — one nullable, un-backfilled column (blizzard#636).

Revision ID: 20260926_1000_hub_runner_declared_subscriptions
Revises: 20260926_1000_operator_garden_proposals
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_1000_hub_runner_declared_subscriptions"
down_revision: str | None = "20260926_1000_operator_garden_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMN = "subscriptions"


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
