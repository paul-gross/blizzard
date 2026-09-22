"""Add the sampler-miss reason and renewal-outcome columns to external_usage_samples
(blizzard#504) — both nullable, unpopulated on every historical row.

Revision ID: 20260922_1200_external_usage_miss_reason
Revises: 20260921_1600_overload_facts
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260922_1200_external_usage_miss_reason"
down_revision = "20260921_1600_overload_facts"
branch_labels = None
depends_on = None

_TABLE = "external_usage_samples"
_MISS_REASON = "miss_reason"
_RENEWAL = "renewal"


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, _TABLE, _MISS_REASON):
        op.add_column(_TABLE, sa.Column(_MISS_REASON, sa.String(), nullable=True))
    if not _has_column(bind, _TABLE, _RENEWAL):
        op.add_column(_TABLE, sa.Column(_RENEWAL, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, _TABLE, _RENEWAL):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_RENEWAL)
    if _has_column(bind, _TABLE, _MISS_REASON):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_MISS_REASON)
