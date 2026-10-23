"""route released cause — which write gave a route's tenure back, and a release's reason.

Adds nullable `route_released.cause` and `route_released.reason`; every existing row reads null. Downgrade drops both.
Revision ID: 20261023_0900_route_released_cause
Revises: 20261022_1000_runner_declining
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_0900_route_released_cause"
down_revision: str | None = "20261022_1000_runner_declining"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RELEASED = "route_released"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (("cause", sa.String()), ("reason", sa.String()))


def _columns(bind: sa.Connection) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(_RELEASED)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy an interrupted batch left (SQLite makes it outside the batch's transaction)."""
    copy = f"_alembic_tmp_{_RELEASED}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps its unnamed ``CHECK`` constraints."""
    return sa.Table(_RELEASED, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    held = _columns(op.get_bind())
    for name, kind in _COLUMNS:
        if name not in held:
            op.add_column(_RELEASED, sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    held = _columns(bind)
    present = [name for name, _ in _COLUMNS if name in held]
    if not present:
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_RELEASED, recreate="auto", copy_from=_reflected(bind)) as batch:
        for name in present:
            batch.drop_column(name)
