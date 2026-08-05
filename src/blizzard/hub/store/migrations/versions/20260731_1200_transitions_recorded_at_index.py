"""transitions.recorded_at index — the activity feed's bounded read (issue #213, hub store tree)

The one high-volume fact table read on a bounded ``recorded_at`` range, unindexed.
Revision ID: 20260731_1200_hub_transitions_recorded_at
Revises: 20260728_1410_hub_chunk_defaults
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_1200_hub_transitions_recorded_at"
down_revision: str | None = "20260728_1410_hub_chunk_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transitions"
_COLUMN = "recorded_at"
_INDEX = "ix_transitions_recorded_at"


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
