"""runner declining — the throttles a runner reported as saying no on its latest registration.

Adds nullable `runner_registrations.declining`; downgrade drops it.
Revision ID: 20261022_1000_runner_declining
Revises: 20261022_1000_runner_agents
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_1000_runner_declining"
down_revision: str | None = "20261022_1000_runner_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNERS = "runner_registrations"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (("declining", sa.Text()),)


def _columns(bind: sa.Connection) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(_RUNNERS)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{_RUNNERS}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps the unnamed ``CHECK`` its own reflection drops."""
    return sa.Table(_RUNNERS, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    bind = op.get_bind()
    held = _columns(bind)
    missing = [(name, kind) for name, kind in _COLUMNS if name not in held]
    if not missing:
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind)) as batch:
        for name, kind in missing:
            batch.add_column(sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    held = _columns(bind)
    present = [name for name, _ in _COLUMNS if name in held]
    if not present:
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind)) as batch:
        for name in present:
            batch.drop_column(name)
