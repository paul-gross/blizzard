"""permit revision — the revision a permit's fact writes compare and set.

Every existing permit starts at revision 1; downgrade drops the column.
Revision ID: 20261103_0900_permit_revision
Revises: 20261015_0900_permits
"""

# exemplar: bzh:manual-migrations, bzh:store-expand-contract, bzh:sql-portable.

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0900_permit_revision"
down_revision: str | None = "20261015_0900_permits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMITS = "permits"
_REVISION = "revision"


def _columns(bind: sa.Connection) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(_PERMITS)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{_PERMITS}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def upgrade() -> None:
    if _REVISION not in _columns(op.get_bind()):
        op.add_column(_PERMITS, sa.Column(_REVISION, sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    bind = op.get_bind()
    if _REVISION not in _columns(bind):
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_PERMITS) as batch:
        batch.drop_column(_REVISION)
