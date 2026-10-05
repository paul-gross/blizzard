"""Record the chunk's latest epoch when a takeover opens, so its hold covers that epoch.

Nullable: a takeover opened before this column holds what its reference lease and fence cover.
Revision ID: 20261004_1200_runner_takeover_hold_epoch
Revises: 20261004_1100_runner_lease_closure_escalation_cause
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_1200_runner_takeover_hold_epoch"
down_revision: str | None = "20261004_1100_runner_lease_closure_escalation_cause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "takeovers"
_COLUMN = "hold_epoch"


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}
    if _COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
