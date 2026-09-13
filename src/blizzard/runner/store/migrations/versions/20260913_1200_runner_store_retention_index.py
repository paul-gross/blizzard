"""Retention's own new read index (Decision 4, issue #520): `external_usage_samples` had
none, so its per-slug newest-attempt prune has nothing to search off without one —
`outbound_buffer` and `heartbeats` already got theirs in `20260913_1100_runner_store_indexes`.

Revision ID: 20260913_1200_runner_store_retention_index
Revises: 20260913_1100_runner_store_indexes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_1200_runner_store_retention_index"
down_revision: str | None = "20260913_1100_runner_store_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_external_usage_samples_slug_sampled_at"
_TABLE = "external_usage_samples"
_COLUMNS = ("slug", "sampled_at")


def _has_index(bind: sa.Connection, table: str, name: str) -> bool:
    return name in {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind, _TABLE, _INDEX_NAME):
        op.create_index(_INDEX_NAME, _TABLE, list(_COLUMNS))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, _TABLE, _INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE)
