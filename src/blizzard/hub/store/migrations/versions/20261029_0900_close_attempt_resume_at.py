"""close attempt resume_at — a close the forge's rate budget turned away waits for its reset.

Adds nullable `close_intent_attempts.resume_at`, set only on a `deferred` attempt; every existing row reads null.
Downgrade deletes the `deferred` rows, then drops the column.
Revision ID: 20261029_0900_close_attempt_resume_at
Revises: 20261027_0900_flows
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261029_0900_close_attempt_resume_at"
down_revision: str | None = "20261027_0900_flows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTEMPTS = "close_intent_attempts"
_COLUMN = "resume_at"


def _held(bind: sa.Connection) -> bool:
    return _COLUMN in {str(c["name"]) for c in sa.inspect(bind).get_columns(_ATTEMPTS)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{_ATTEMPTS}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps what its own reflection would drop."""
    return sa.Table(_ATTEMPTS, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    if not _held(op.get_bind()):
        op.add_column(_ATTEMPTS, sa.Column(_COLUMN, UtcDateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _held(bind):
        return
    op.execute(sa.text(f"DELETE FROM {_ATTEMPTS} WHERE outcome = 'deferred'"))
    _clear_stale_copy(bind)
    with op.batch_alter_table(_ATTEMPTS, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.drop_column(_COLUMN)
