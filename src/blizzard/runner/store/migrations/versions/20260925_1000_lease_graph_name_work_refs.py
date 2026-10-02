"""lease_context graph name and work refs — what the mint's envelope named (runner store tree)

Two guarded, nullable columns on ``lease_context``: un-backfilled, so NULL means unknown.
``work_refs`` holds a JSON array, so a mint with no refs reads ``[]`` and an earlier row reads NULL.
Revision ID: 20260925_1000_runner_lease_graph_name_work_refs
Revises: 20260924_0500_in_flight_elicitations_autoincrement
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260925_1000_runner_lease_graph_name_work_refs"
down_revision: str | None = "20260924_0500_in_flight_elicitations_autoincrement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lease_context"
_COLUMNS = (("graph_name", sa.String()), ("work_refs", sa.Text()))


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    for name, type_ in _COLUMNS:
        if name not in present:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        for name, _type in _COLUMNS:
            if name in present:
                batch.drop_column(name)
