"""lease_context session stamps — the session name, model, and effort (runner store tree)

Three guarded, nullable columns on ``lease_context``: un-backfilled, so NULL means unknown.
Revision ID: 20260728_1500_runner_lease_session_stamps
Revises: 20260727_1000_runner_session_preamble_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_1500_runner_lease_session_stamps"
down_revision: str | None = "20260727_1000_runner_session_preamble_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lease_context"
_COLUMNS = ("session_name", "resolved_model", "resolved_effort")


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    for name in _COLUMNS:
        if name not in present:
            op.add_column(_TABLE, sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        for name in _COLUMNS:
            if name in present:
                batch.drop_column(name)
