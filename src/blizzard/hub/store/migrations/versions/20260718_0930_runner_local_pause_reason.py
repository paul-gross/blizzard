"""runner local pause reason — the local-pause fact's cause column, nullable so a manual
pause and every pre-#61 row read back bare (hub store tree, issue #61)

Revision ID: 20260718_0930_hub_runner_local_pause_reason
Revises: 20260717_2330_hub_usage_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0930_hub_runner_local_pause_reason"
down_revision: str | None = "20260717_2330_hub_usage_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_local_pause_facts"
_COLUMN = "reason"


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
