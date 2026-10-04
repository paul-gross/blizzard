"""Record why an escalating closure escalated, beside its reason.

Nullable: a closure that escalated nothing, or one written before this column, carries none.
Revision ID: 20261004_1100_runner_lease_closure_escalation_cause
Revises: 20261004_1000_runner_invocation_boundary_advances
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_1100_runner_lease_closure_escalation_cause"
down_revision: str | None = "20261004_1000_runner_invocation_boundary_advances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lease_closures"
_COLUMN = "escalation_cause"


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}
    if _COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
