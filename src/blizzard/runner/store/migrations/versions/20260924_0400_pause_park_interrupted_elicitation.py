"""Add the interrupted-elicitation column to pause_parks (blizzard#627) — nullable, unpopulated
on every historical row: a park recorded before it owes no elicitation teardown.

Revision ID: 20260924_0400_pause_park_interrupted_elicitation
Revises: 20260922_1200_external_usage_miss_reason
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260924_0400_pause_park_interrupted_elicitation"
down_revision = "20260922_1200_external_usage_miss_reason"
branch_labels = None
depends_on = None

_TABLE = "pause_parks"
_COLUMN = "interrupted_elicitation_id"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    if not _has_column(op.get_bind()):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column(op.get_bind()):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
