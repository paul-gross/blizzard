"""escalation cause and detail (hub store tree)

Adds nullable ``escalations.cause``/``detail``, idempotently, with no backfill.
Revision ID: 20261003_1300_escalation_cause
Revises: 20261003_1200_event_drops
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261003_1300_escalation_cause"
down_revision: str | None = "20261003_1200_event_drops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "escalations"
_COLUMNS = ("cause", "detail")


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    present = _columns(op.get_bind())
    for column in _COLUMNS:
        if column not in present:
            op.add_column(_TABLE, sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    present = _columns(op.get_bind())
    for column in _COLUMNS:
        if column in present:
            op.drop_column(_TABLE, column)
