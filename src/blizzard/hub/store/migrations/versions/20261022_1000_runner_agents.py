"""runner agents — the shared agent count a runner declares at registration.

Adds nullable `runner_registrations.agents`; downgrade drops it.
Revision ID: 20261022_1000_runner_agents
Revises: 20261021_0900_human_notes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_1000_runner_agents"
down_revision: str | None = "20261021_0900_human_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNERS = "runner_registrations"
_COLUMN = "agents"


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
    if _COLUMN in _columns(bind):
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.drop_column(_COLUMN)
