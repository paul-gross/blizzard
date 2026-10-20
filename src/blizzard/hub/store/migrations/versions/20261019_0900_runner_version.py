"""runner version — the window's judgement of a runner's latest registration.

Adds nullable `runner_registrations.reported_version`, `version_refusal` and `version_judged_at`; downgrade drops them.
Revision ID: 20261019_0900_runner_version
Revises: 20261018_1200_project_color
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261019_0900_runner_version"
down_revision: str | None = "20261018_1200_project_color"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNERS = "runner_registrations"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (
    ("reported_version", sa.Text()),
    ("version_refusal", sa.String()),
    ("version_judged_at", UtcDateTime()),
)


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
