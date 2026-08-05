"""escalation takeover command (hub store tree) — adds ``escalations.takeover_command``,
the pasteable resume a human runs to enter a parked session. Idempotent (P7).

Revision ID: 20260713_1424_hub_escalation_takeover
Revises: 20260713_1218_hub_walking_skeleton
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_1424_hub_escalation_takeover"
down_revision: str | None = "20260713_1218_hub_walking_skeleton"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "escalations"
_COLUMN = "takeover_command"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
