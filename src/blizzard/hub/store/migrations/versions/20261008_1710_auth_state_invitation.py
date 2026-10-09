"""auth state invitation — a provider sign-in carries the invitation it was started with.

Adds the nullable ``auth_state.invitation_token_hash``: the sha256 hex of the invitation token a ``provider_login``
row was started with, NULL for every other row. No row is backfilled — every row already in flight was started without
an invitation. A store whose ``auth_state`` already holds the column skips the add.

The downgrade drops the column; only invited sign-ins in flight at that moment — rows minutes from expiry — lose
their invitation, and finish as plain sign-ins.
Revision ID: 20261008_1710_auth_state_invitation
Revises: 20261008_1700_tenant_identity_move
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1710_auth_state_invitation"
down_revision: str | None = "20261008_1700_tenant_identity_move"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "auth_state"
_COLUMN = "invitation_token_hash"


def _present(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    if not _present(op.get_bind()):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    if _present(op.get_bind()):
        with op.batch_alter_table(_TABLE, recreate="auto") as batch:
            batch.drop_column(_COLUMN)
