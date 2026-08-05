"""escalation wrapped takeover command (hub store tree, issue #251)

Adds ``escalations.wrapped_takeover_command``, idempotently — a fresh store's create-all has it.
Revision ID: 20260803_1000_hub_escalation_wrapped_takeover
Revises: 20260801_1600_hub_runner_external_usage
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_1000_hub_escalation_wrapped_takeover"
down_revision: str | None = "20260801_1600_hub_runner_external_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "escalations"
_COLUMN = "wrapped_takeover_command"


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
