"""runner_identity_tenant — the identity row keeps, beside the runner's id, the tenant the hub answered with.

``runner_identity`` gains nullable ``tenant_id`` and ``tenant_name``, for display only: nothing keys or
filters on them, and no other table gains a tenant. A row written before this revision carries none
until the runner's first contact with the hub after the upgrade fills both. Re-running skips a column
already present. ``downgrade()`` drops both columns in one batch; the tenant comes back at the next
contact after a later upgrade.
Revision ID: 20261008_1000_runner_identity_tenant
Revises: 20261006_1000_runner_identity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1000_runner_identity_tenant"
down_revision: str | None = "20261006_1000_runner_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_identity"
_COLUMNS = ("tenant_id", "tenant_name")


def _present(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    present = _present(op.get_bind())
    for column in _COLUMNS:
        if column not in present:
            op.add_column(_TABLE, sa.Column(column, sa.String(), nullable=True))


def downgrade() -> None:
    dropped = [c for c in _COLUMNS if c in _present(op.get_bind())]
    if not dropped:
        return
    with op.batch_alter_table(_TABLE) as batch:
        for column in dropped:
            batch.drop_column(column)
