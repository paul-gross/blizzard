"""work_item_proposals.runner_id — the proposing runner (D4), stamped at insert.
Nullable, no backfill.

Revision ID: 20260825_1150_work_item_proposals_runner_id
Revises: 20260825_1100_work_item_proposals
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_1150_work_item_proposals_runner_id"
down_revision: str | None = "20260825_1100_work_item_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "work_item_proposals"
_COLUMN = "runner_id"


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
