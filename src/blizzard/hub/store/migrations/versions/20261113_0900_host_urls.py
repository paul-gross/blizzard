"""host urls — every origin a host answers on, beside its one canonical ``url``.

Adds the nullable ``hosts.urls`` column (JSON ``list[str]``); a rerun skips it. A row written before the column keeps
``NULL``, which reads as the host's one ``url``. The downgrade drops the column.
Revision ID: 20261113_0900_host_urls
Revises: 20261113_0900_credentials
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_0900_host_urls"
down_revision: str | None = "20261113_0900_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "hosts"
_COLUMN = "urls"


def _held(bind: sa.Connection) -> bool:
    return _COLUMN in {str(c["name"]) for c in sa.inspect(bind).get_columns(_TABLE)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{_TABLE}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps the constraints its own reflection drops."""
    return sa.Table(_TABLE, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    bind = op.get_bind()
    if _held(bind):
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_TABLE, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _held(bind):
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_TABLE, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.drop_column(_COLUMN)
