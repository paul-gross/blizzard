"""runner bindings — the bindings and subscription a runner declares at registration.

Adds nullable `runner_registrations.bindings` and `runner_registrations.subscription`; downgrade drops them.
Revision ID: 20261012_1100_runner_bindings
Revises: 20261012_1000_hosts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_1100_runner_bindings"
down_revision: str | None = "20261012_1000_hosts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNERS = "runner_registrations"
_COLUMNS = ("bindings", "subscription")


def _columns(bind: sa.Connection) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(_RUNNERS)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a batch left behind when its run stopped part way (SQLite makes it outside the batch's
    transaction); the name is the one Alembic's batch gives it."""
    copy = f"_alembic_tmp_{_RUNNERS}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps its unnamed ``CHECK`` constraints."""
    return sa.Table(_RUNNERS, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    bind = op.get_bind()
    missing = [name for name in _COLUMNS if name not in _columns(bind)]
    if not missing:
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind)) as batch:
        for name in missing:
            batch.add_column(sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = [name for name in _COLUMNS if name in _columns(bind)]
    if not present:
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind)) as batch:
        for name in present:
            batch.drop_column(name)
